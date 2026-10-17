"""Read an address book over CardDAV (iCloud, Google, Fastmail, Nextcloud…) with an app password.

Read-only: only PROPFIND and REPORT requests are sent, never PUT or DELETE, so nothing in the user's contacts
can change. Discovery follows RFC 6764/6352: the server's principal, then its address-book home, then every
address book in it; cards are fetched with ``addressbook-multiget`` in batches, which every major server supports.
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import httpx

from ..tools.registry import ToolError

DAV, CARD = "DAV:", "urn:ietf:params:xml:ns:carddav"
NS = {"d": DAV, "c": CARD}
PRESETS = {
    "icloud": {"label": "iCloud (iPhone)", "url": "https://contacts.icloud.com/",
               "help": "Create an app-specific password at account.apple.com → Sign-In and Security."},
    "google": {"label": "Google (Android)", "url": "https://www.googleapis.com/carddav/v1/principals/{user}/lists/default/",
               "help": "Turn on 2-Step Verification, then create an app password at myaccount.google.com/apppasswords. "
                       "If Google refuses it, export your contacts as a file and import that instead."},
    "custom": {"label": "Other (CardDAV)", "url": "", "help": "Use your provider's CardDAV address."},
}
BATCH = 100


def _propfind(depth: str, *props: str) -> str:
    inner = "".join(f"<{p}/>" for p in props)
    return f'<?xml version="1.0"?><d:propfind xmlns:d="{DAV}" xmlns:c="{CARD}"><d:prop>{inner}</d:prop></d:propfind>'


class CardDAV:
    def __init__(self, url: str, user: str, password: str, transport: httpx.BaseTransport | None = None) -> None:
        self.url = url.replace("{user}", urllib.parse.quote(user))
        self.client = httpx.Client(auth=(user, password), timeout=30, transport=transport, follow_redirects=True,
                                   headers={"User-Agent": "ORION"})

    def __enter__(self) -> CardDAV:
        return self

    def __exit__(self, *exc) -> None:
        self.client.close()

    def _request(self, method: str, url: str, body: str, depth: str) -> list[ET.Element]:
        try:
            r = self.client.request(method, url, content=body.encode(),
                                    headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
        except httpx.HTTPError as exc:
            raise ToolError(f"Couldn't reach the contacts server: {type(exc).__name__}.") from exc
        if r.status_code in (401, 403):
            raise ToolError("The contacts server refused the sign-in. Check the address and use an app password "
                            "(not your normal password).")
        if r.status_code >= 400:
            raise ToolError(f"The contacts server answered {r.status_code} to {method}.")
        try:
            return ET.fromstring(r.content).findall("d:response", NS)
        except ET.ParseError as exc:
            raise ToolError("The contacts server sent something that isn't CardDAV; check the address.") from exc

    def _href(self, el: ET.Element | None, base: str) -> str:
        href = el.findtext("d:href", namespaces=NS) if el is not None else None
        return urllib.parse.urljoin(base, href.strip()) if href else ""

    def address_books(self) -> list[str]:
        """URLs of every address book the account can read."""
        url = self.url
        for resp in self._request("PROPFIND", url, _propfind("0", "d:resourcetype", "d:current-user-principal"), "0"):
            if resp.find(".//d:resourcetype/c:addressbook", NS) is not None:
                return [url]  # already pointed at an address book (e.g. Google's lists/default)
            principal = self._href(resp.find(".//d:current-user-principal", NS), url)
            if principal:
                url = principal
        home = ""
        for resp in self._request("PROPFIND", url, _propfind("0", "c:addressbook-home-set"), "0"):
            home = home or self._href(resp.find(".//c:addressbook-home-set", NS), url)
        if not home:
            raise ToolError("That server didn't say where its address books are; check the CardDAV address.")
        books = [self._href(r, home) for r in self._request("PROPFIND", home, _propfind("1", "d:resourcetype"), "1")
                 if r.find(".//d:resourcetype/c:addressbook", NS) is not None]
        if not books:
            raise ToolError("No address books were found in that account.")
        return books

    def cards(self) -> list[str]:
        """Every vCard in every address book, as text."""
        out: list[str] = []
        for book in self.address_books():
            hrefs = [r.findtext("d:href", namespaces=NS).strip()
                     for r in self._request("PROPFIND", book, _propfind("1", "d:getetag"), "1")
                     if r.findtext("d:href", namespaces=NS)
                     and urllib.parse.urljoin(book, r.findtext("d:href", namespaces=NS).strip()).rstrip("/")
                     != book.rstrip("/")]
            for i in range(0, len(hrefs), BATCH):
                refs = "".join(f"<d:href>{escape(h)}</d:href>" for h in hrefs[i:i + BATCH])
                body = (f'<?xml version="1.0"?><c:addressbook-multiget xmlns:d="{DAV}" xmlns:c="{CARD}">'
                        f"<d:prop><d:getetag/><c:address-data/></d:prop>{refs}</c:addressbook-multiget>")
                out += [t for r in self._request("REPORT", book, body, "1")
                        if (t := r.findtext(".//c:address-data", namespaces=NS))]
        return out
