"""Browser toolkit: ORION drives a real browser window (Microsoft Edge on Windows, no download needed).

* A separate, persistent ORION browser profile: sign in to a site once (you type the password), and
  ORION stays signed in next time. Your everyday browser profile is never touched.
* Pages are described to the model as a numbered list of the things you can click or type into
  (refs), plus readable text. Actions target refs, which is far more reliable than pixel clicking.
* Reading and navigating are free; clicking, typing and submitting ask for approval (allow per session).
  ORION never types into password fields; you do that yourself in the window.
* Playwright runs on one dedicated thread, so a slow website can never stall the rest of ORION.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Literal

from .platform import IS_WIN
from .registry import ToolContext, ToolError, tool

KIT = "browser"
NAV_TIMEOUT = 30_000
ACTION_TIMEOUT = 10_000

SNAPSHOT_JS = r"""
(max) => {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [onclick], [contenteditable=""],' +
    '[contenteditable=true], [role=button], [role=link], [role=checkbox], [role=radio], [role=tab], [role=menuitem],' +
    '[role=option], [role=switch], [role=combobox], [role=textbox], [role=searchbox]';
  document.querySelectorAll('[data-orion-ref]').forEach(e => e.removeAttribute('data-orion-ref'));
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
    n += 1; el.setAttribute('data-orion-ref', String(n));
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    let role = el.getAttribute('role') || ({a: 'link', select: 'select', textarea: 'textbox', summary: 'button'}[tag]) ||
      (tag === 'input' ? (['checkbox', 'radio', 'submit', 'button', 'file', 'range', 'date'].includes(type) ? type : 'textbox') : tag);
    if (tag === 'input' && type === 'password') role = 'password';
    let name = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) ||
      el.getAttribute('placeholder') || el.getAttribute('title') || el.innerText || el.getAttribute('alt') ||
      (tag === 'input' && ['submit', 'button'].includes(type) ? el.value : '') || el.getAttribute('name') || '';
    if (!name.trim()) { const img = el.querySelector('img[alt]'); if (img) name = img.getAttribute('alt'); }
    const item = {ref: n, role, name: name.replace(/\s+/g, ' ').trim().slice(0, 90)};
    if (tag === 'input' || tag === 'textarea') item.value = type === 'password' ? (el.value ? '(filled)' : '') : (el.value || '').slice(0, 60);
    if (type === 'checkbox' || type === 'radio') item.checked = el.checked;
    if (tag === 'select') item.value = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '';
    if (tag === 'a') item.href = (el.getAttribute('href') || '').slice(0, 90);
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') item.disabled = true;
    if (r.bottom < 0 || r.top > innerHeight) item.offscreen = true;
    out.push(item);
    if (out.length >= max) break;
  }
  return out;
}
"""


def _data_dir() -> Path:
    from . import files

    root = files._data_dir or (Path.home() / ".orion")
    return root / "browser-profile"


class Browser:
    """One persistent browser window, owned by a single worker thread."""

    def __init__(self) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")
        self._pw: Any = None
        self._ctx: Any = None
        self._page: Any = None
        self.names: dict[int, str] = {}  # ref → label from the latest snapshot (for approval summaries)
        self.host = ""
        self._lock = threading.Lock()

    # Everything that touches Playwright runs on the worker thread.
    def call(self, fn: Callable[[], Any], timeout: float = 90) -> Any:
        fut = self._pool.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise ToolError("The browser didn't respond in time.") from exc

    def _launch(self) -> None:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import sync_playwright

        if self._pw is None:
            self._pw = sync_playwright().start()
        profile = _data_dir()
        profile.mkdir(parents=True, exist_ok=True)
        opts: dict[str, Any] = {"headless": os.environ.get("ORION_BROWSER_HEADLESS") == "1", "no_viewport": True,
                                "args": ["--start-maximized", "--no-first-run", "--no-default-browser-check"],
                                "accept_downloads": True}
        exe = os.environ.get("ORION_BROWSER_PATH")
        # Keep the browser's security sandbox on (Playwright turns it off by default, which also shows a warning bar).
        opts["chromium_sandbox"] = not exe
        attempts = [{"executable_path": exe}] if exe else ([{"channel": "msedge"}, {"channel": "chrome"}] if IS_WIN
                                                         else [{"channel": "chrome"}, {"channel": "msedge"}, {}])
        last: Exception | None = None
        for extra in attempts:
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(str(profile), **opts, **extra)
                break
            except PwError as exc:
                last = exc
                if "ProcessSingleton" in str(exc) or "already in use" in str(exc):
                    raise ToolError("ORION's browser profile is open in another window; close it and try again.") from exc
        else:
            raise ToolError(f"Couldn't start a browser (Microsoft Edge or Chrome). {str(last)[:200]}")
        self._ctx.set_default_timeout(ACTION_TIMEOUT)
        self._ctx.set_default_navigation_timeout(NAV_TIMEOUT)
        self._ctx.on("page", self._on_page)
        self._ctx.on("close", lambda *_: self._reset())
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def _reset(self) -> None:
        self._ctx = None
        self._page = None
        self.names = {}

    def _on_page(self, page: Any) -> None:
        self._page = page  # follow pop-ups and links that open a new tab

    def page(self) -> Any:
        if self._ctx is None:
            self._launch()
        if self._page is None or self._page.is_closed():
            pages = [p for p in self._ctx.pages if not p.is_closed()]
            self._page = pages[-1] if pages else self._ctx.new_page()
        return self._page

    def settle(self, page: Any) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass  # busy pages never go idle; what's loaded is enough

    def snapshot(self, page: Any, limit: int = 80) -> list[dict]:
        items = page.evaluate(SNAPSHOT_JS, limit)
        self.names = {i["ref"]: f"{i['role']} “{i['name']}”" for i in items}
        self.host = re.sub(r"^www\.", "", re.sub(r"^\w+://([^/]+).*$", r"\1", page.url))
        return items

    def state(self, page: Any, elements: int = 60, text_chars: int = 1500, find: str = "") -> dict:
        items = self.snapshot(page, 250)
        text = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
        text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
        if find:
            f = find.lower()
            items = [i for i in items if f in i["name"].lower() or f in str(i.get("value", "")).lower()
                     or f in str(i.get("href", "")).lower()]
            paras = [p for p in text.split("\n") if f in p.lower()]
            text = "\n".join(paras)
        shown = [i for i in items if not i.get("offscreen")][:elements]
        shown += [i for i in items if i.get("offscreen")][: max(0, elements - len(shown))]
        out = {"title": page.title(), "url": page.url, "elements": shown,
               "text": text[:text_chars] + ("…" if len(text) > text_chars else "")}
        if len(items) > len(shown):
            out["more"] = f"{len(items) - len(shown)} more elements; use browser_view with find= to filter."
        return out

    def locate(self, page: Any, ref: int, text: str) -> Any:
        if ref:
            loc = page.locator(f'[data-orion-ref="{int(ref)}"]')
            if loc.count() == 0:
                raise ToolError(f"Element {ref} isn't on the page any more; call browser_view for fresh numbers.")
            return loc.first
        if text:
            for loc in (page.get_by_role("button", name=text), page.get_by_role("link", name=text),
                        page.get_by_label(text), page.get_by_placeholder(text), page.get_by_text(text)):
                try:
                    if loc.count():
                        return loc.first
                except Exception:
                    continue
            raise ToolError(f"Nothing called “{text}” on the page.")
        raise ToolError("Give the element's number (ref) from browser_view, or its visible text.")

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
        self._reset()


BROWSER = Browser()


def _label(a: dict) -> str:
    ref = a.get("ref") or 0
    what = BROWSER.names.get(int(ref)) if ref else f"“{a.get('text')}”"
    return f"{what or f'element {ref}'}" + (f" on {BROWSER.host}" if BROWSER.host else "")


def _normalise_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-z][\w+.-]*:", url, re.I):
        url = "https://" + url if "." in url and " " not in url else "https://duckduckgo.com/?q=" + url.replace(" ", "+")
    return url


@tool(category="browser", kit=KIT)
def browser_open(url: str, new_tab: bool = False) -> dict:
    """Open a web page in ORION's browser window and see what's on it (numbered elements + text).
    Use this for anything you need to *do* on a website: sign-ups, forms, bookings, shopping, dashboards.
    For just reading an article or looking something up, web_search/read_webpage are faster.

    Args:
        url: Address (https://…), domain ("amazon.com") or words to search for.
        new_tab: Open in a new tab instead of the current one.
    """
    target = _normalise_url(url)

    def go() -> dict:
        page = BROWSER.page()
        if new_tab:
            page = BROWSER._ctx.new_page()
            BROWSER._page = page
        try:
            page.goto(target, wait_until="domcontentloaded")
        except Exception as exc:
            raise ToolError(f"Couldn't open {target}: {str(exc).splitlines()[0][:200]}") from exc
        BROWSER.settle(page)
        page.bring_to_front()
        return BROWSER.state(page)

    return BROWSER.call(go)


@tool(category="browser", kit=KIT)
def browser_view(find: str = "", elements: int = 80, text_chars: int = 4000) -> dict:
    """Describe the current browser page: title, address, numbered interactive elements (ref, role, name,
    value) and readable text. Call it again after anything changes; refs are renumbered each time.

    Args:
        find: Only show elements/text containing this (e.g. "price", "checkout").
        elements: Max elements to list.
        text_chars: Max characters of page text.
    """
    def view() -> dict:
        page = BROWSER.page()
        return BROWSER.state(page, elements, text_chars, find)

    return BROWSER.call(view)


def _act(action: Callable[[Any], str]) -> dict:
    def run() -> dict:
        page = BROWSER.page()
        pages_before = len(BROWSER._ctx.pages)
        try:
            note = action(page)
        except ToolError:
            raise
        except Exception as exc:
            msg = str(exc).splitlines()[0][:220]
            raise ToolError(f"That didn't work: {msg}. Call browser_view to re-check the page.") from exc
        page.wait_for_timeout(300)  # let pop-up / new-tab events arrive
        pages = [p for p in BROWSER._ctx.pages if not p.is_closed()]
        if len(pages) > pages_before:
            BROWSER._page = pages[-1]  # follow the tab the click opened
        page = BROWSER.page()
        BROWSER.settle(page)
        out = BROWSER.state(page, 50, 1200)
        out["done"] = note + (" (it opened a new tab)" if len(BROWSER._ctx.pages) > pages_before else "")
        return out

    return BROWSER.call(run)


@tool(risk="confirm", category="browser", kit=KIT, summary=lambda a: f"Click {_label(a)}")
def browser_click(ref: int = 0, text: str = "") -> dict:
    """Click a link, button, checkbox, tab or menu item on the current page.

    Args:
        ref: The element's number from browser_view/browser_open.
        text: Or the visible text of what to click (if you have no ref).
    """
    def click(page: Any) -> str:
        loc = BROWSER.locate(page, ref, text)
        loc.scroll_into_view_if_needed()
        loc.click()
        return f"Clicked {BROWSER.names.get(ref) or text}."

    return _act(click)


@tool(risk="confirm", category="browser", kit=KIT,
      summary=lambda a: f"Type “{str(a.get('text', ''))[:40]}” into {_label(a)}" + (" and submit" if a.get("submit") else ""))
def browser_type(text: str, ref: int = 0, field: str = "", submit: bool = False, clear: bool = True) -> dict:
    """Type into a text box on the current page (search boxes, forms, messages). Never use it for passwords:
    ask the user to type those into the browser window themselves.

    Args:
        text: What to type.
        ref: The field's number from browser_view.
        field: Or the field's label/placeholder text.
        submit: Press Enter afterwards (e.g. to search).
        clear: Replace what's already in the field.
    """
    def type_(page: Any) -> str:
        loc = BROWSER.locate(page, ref, field)
        if (loc.get_attribute("type") or "").lower() == "password":
            raise ToolError("That's a password field. Ask the user to type their password into the browser window.")
        loc.scroll_into_view_if_needed()
        if clear:
            loc.fill(text)
        else:
            loc.click()
            page.keyboard.type(text, delay=15)
        if submit:
            loc.press("Enter")
        return f"Typed into {BROWSER.names.get(ref) or field}" + (" and pressed Enter." if submit else ".")

    return _act(type_)


@tool(risk="confirm", category="browser", kit=KIT, summary=lambda a: f"Choose “{a.get('option')}” in {_label(a)}")
def browser_select(option: str, ref: int = 0, field: str = "") -> dict:
    """Choose an option in a drop-down list on the current page.

    Args:
        option: The option's visible text (or value).
        ref: The drop-down's number from browser_view.
        field: Or the drop-down's label.
    """
    def select(page: Any) -> str:
        loc = BROWSER.locate(page, ref, field)
        try:
            loc.select_option(label=option)
        except Exception:
            loc.select_option(value=option)
        return f"Selected “{option}”."

    return _act(select)


@tool(risk="confirm", category="browser", kit=KIT, summary=lambda a: f"Press {a.get('keys')} in the browser")
def browser_press(keys: str) -> dict:
    """Press a key or shortcut in the browser page, e.g. "Enter", "Escape", "Tab", "PageDown", "Control+A".

    Args:
        keys: Playwright key name(s), e.g. "Enter" or "Control+Shift+R".
    """
    def press(page: Any) -> str:
        page.keyboard.press(keys)
        return f"Pressed {keys}."

    return _act(press)


@tool(category="browser", kit=KIT)
def browser_scroll(direction: Literal["down", "up", "top", "bottom"] = "down") -> dict:
    """Scroll the current page to reveal more content (then see the newly visible elements).

    Args:
        direction: Which way.
    """
    js = {"down": "window.scrollBy(0, innerHeight * 0.85)", "up": "window.scrollBy(0, -innerHeight * 0.85)",
          "top": "window.scrollTo(0, 0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)"}[direction]

    def scroll(page: Any) -> str:
        page.evaluate(js)
        page.wait_for_timeout(400)
        return f"Scrolled {direction}."

    return _act(scroll)


@tool(category="browser", kit=KIT)
def browser_back() -> dict:
    """Go back to the previous page in the browser."""
    def back(page: Any) -> str:
        page.go_back(wait_until="domcontentloaded")
        return "Went back."

    return _act(back)


@tool(category="browser", kit=KIT)
def browser_tabs(action: Literal["list", "switch", "close"] = "list", index: int = 0) -> list[dict] | dict:
    """List the browser's tabs, switch to one or close one.

    Args:
        action: list, switch or close.
        index: The tab number from the list (for switch/close).
    """
    def tabs() -> Any:
        BROWSER.page()
        pages = [p for p in BROWSER._ctx.pages if not p.is_closed()]
        if action == "list":
            return [{"index": i, "title": p.title()[:80], "url": p.url[:120], "current": p is BROWSER._page}
                    for i, p in enumerate(pages)]
        if not 0 <= index < len(pages):
            raise ToolError(f"No tab {index}; there are {len(pages)}.")
        if action == "switch":
            BROWSER._page = pages[index]
            pages[index].bring_to_front()
            return BROWSER.state(pages[index], 50, 1200)
        pages[index].close()
        BROWSER._page = None
        return {"done": f"Closed tab {index}."}

    return BROWSER.call(tabs)


@tool(category="browser", kit=KIT)
async def browser_look(question: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Look at the browser page visually (layout, images, charts, seat maps, prices shown in pictures) and answer
    a question about it.

    Args:
        question: What you want to know, e.g. "which seats are still free?".
    """
    import asyncio

    if ctx is None or ctx.engine is None:
        raise ToolError("Vision isn't available here.")
    shot = await asyncio.to_thread(BROWSER.call, lambda: BROWSER.page().screenshot(type="jpeg", quality=70))
    return await ctx.engine.brain.vision(question, shot, "image/jpeg")


@tool(category="browser", kit=KIT)
def browser_close() -> str:
    """Close ORION's browser window (sign-ins are kept for next time)."""
    BROWSER.call(BROWSER.close)
    return "Browser closed."
