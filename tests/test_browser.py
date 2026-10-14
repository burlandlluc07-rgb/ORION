"""Browser toolkit against a real (headless) Chromium and a local test site."""

import http.server
import os
import shutil
import threading

import pytest

from orion.tools import browser, files
from orion.tools.registry import ToolError

EXE = os.environ.get("ORION_TEST_BROWSER") or ("/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium")
                                              else shutil.which("chromium") or shutil.which("google-chrome"))
pytestmark = pytest.mark.skipif(not EXE, reason="no Chromium available for browser tests")

PAGES = {
    "/": """<html><head><title>Shop</title></head><body>
        <h1>Welcome to the test shop</h1><p>Price: €19.99</p>
        <a href="/form">Sign up</a> <a href="/new" target="_blank">Open offer</a>
        <button onclick="document.getElementById('msg').innerText='Added!'">Add to cart</button>
        <div id="msg"></div><div style="height:3000px"></div><button>Bottom button</button></body></html>""",
    "/form": """<html><head><title>Sign up</title></head><body>
        <form action="/done" method="get">
          <label>Name <input name="name"></label>
          <label>Password <input type="password" name="pw"></label>
          <label>Country <select name="c"><option value="es">Spain</option><option value="uk">United Kingdom</option></select></label>
          <label><input type="checkbox" name="news"> Newsletter</label>
          <button type="submit">Create account</button></form></body></html>""",
    "/new": "<html><head><title>Offer</title></head><body>Special offer page</body></html>",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        body = PAGES.get(path, f"<html><head><title>Done</title></head><body>Thanks! {self.path}</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def chrome(tmp_path_factory):
    old = {k: os.environ.get(k) for k in ("ORION_BROWSER_PATH", "ORION_BROWSER_HEADLESS")}
    os.environ["ORION_BROWSER_PATH"], os.environ["ORION_BROWSER_HEADLESS"] = EXE, "1"
    files.set_data_dir(tmp_path_factory.mktemp("orion"))
    yield browser.BROWSER
    browser.browser_close()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def ref_of(state, name):
    return next(e["ref"] for e in state["elements"] if name.lower() in e["name"].lower())


def test_browse_click_type_select_submit(site, chrome):
    st = browser.browser_open(site)
    assert st["title"] == "Shop" and "€19.99" in st["text"]
    st = browser.browser_click(ref=ref_of(st, "Add to cart"))
    assert "Added!" in st["text"]
    assert "Bottom button" in [e["name"] for e in browser.browser_view(find="bottom")["elements"]]

    st = browser.browser_click(text="Sign up")
    assert st["title"] == "Sign up"
    pw = next(e for e in st["elements"] if e["role"] == "password")
    with pytest.raises(ToolError, match="password"):
        browser.browser_type("hunter2", ref=pw["ref"])
    st = browser.browser_type("Lluc", ref=ref_of(st, "Name"))
    st = browser.browser_select("United Kingdom", ref=next(e["ref"] for e in st["elements"] if e["role"] == "select"))
    st = browser.browser_click(ref=next(e["ref"] for e in st["elements"] if e["role"] == "checkbox"))
    assert next(e for e in st["elements"] if e["role"] == "checkbox")["checked"] is True
    st = browser.browser_click(text="Create account")
    assert st["title"] == "Done" and "name=Lluc" in st["text"] and "c=uk" in st["text"] and "news=on" in st["text"]

    st = browser.browser_back()
    assert st["title"] == "Sign up"


def test_new_tabs_and_errors(site, chrome):
    st = browser.browser_open(site)
    st = browser.browser_click(text="Open offer")
    assert st["title"] == "Offer" and "new tab" in st["done"]
    tabs = browser.browser_tabs()
    assert len(tabs) >= 2 and any(t["current"] and t["title"] == "Offer" for t in tabs)
    browser.browser_tabs("switch", index=0)
    with pytest.raises(ToolError):
        browser.browser_click(ref=9999)
    with pytest.raises(ToolError):
        browser.browser_click(text="No such button anywhere")


def test_approval_summary_names_the_element(site, chrome):
    st = browser.browser_open(site)
    ref = ref_of(st, "Add to cart")
    from orion.tools.registry import decorated_tools

    t = next(t for t in decorated_tools() if t.name == "browser_click")
    assert t.describe_call({"ref": ref}) == "Click button “Add to cart” on 127.0.0.1:" + site.rsplit(":", 1)[1]
    assert t.risk == "confirm" and t.kit == "browser"


def test_url_normalisation():
    assert browser._normalise_url("amazon.com") == "https://amazon.com"
    assert browser._normalise_url("best pizza near me").startswith("https://duckduckgo.com/?q=best+pizza")
    assert browser._normalise_url("http://x.org/a") == "http://x.org/a"
