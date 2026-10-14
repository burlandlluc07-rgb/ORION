"""Web tools that cost zero Gemini quota: search (DuckDuckGo), page reading, weather (Open-Meteo), media."""

from __future__ import annotations

import html
import re
import time
import urllib.parse
from typing import Literal

import httpx

from .platform import open_target
from .registry import ToolError, tool

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 ORION/2"


def _ddgs():
    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError as exc:
            raise ToolError("Web search needs the 'ddgs' package.") from exc
    return DDGS()


@tool(category="web")
def web_search(query: str, max_results: int = 6, kind: Literal["web", "news"] = "web") -> list[dict]:
    """Search the internet for current information. Use for facts you're unsure of, prices, scores, news, etc.

    Args:
        query: Search query.
        max_results: Number of results (1-10).
        kind: "web" for general results, "news" for recent news articles.
    """
    n = max(1, min(max_results, 10))
    raw: list[dict] = []
    # News often has no hits for broad queries: fall back to a normal web search instead of failing.
    for k in ([kind, "web"] if kind == "news" else [kind]):
        for attempt in range(2):
            try:
                with _ddgs() as d:
                    raw = list(d.news(query, max_results=n) if k == "news" else d.text(query, max_results=n))
                break
            except Exception as exc:  # ddgs raises on "no results" and on transient backend errors
                if "no results" in str(exc).lower():
                    break
                if attempt == 1:
                    raise ToolError(f"Web search is unavailable right now ({exc}).") from exc
        if raw:
            kind = k  # type: ignore[assignment]
            break
    out = []
    for r in raw:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("href") or r.get("url", ""),
            "snippet": (r.get("body") or r.get("excerpt") or "")[:300],
            **({"date": r.get("date", "")} if kind == "news" else {}),
        })
    if not out:
        raise ToolError("No results.")
    return out


def html_to_text(raw: str) -> tuple[str, str]:
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    body = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form|aside)[^>]*>.*?</\1>", " ", raw)
    body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    return (html.unescape(title.group(1).strip()) if title else ""), body


@tool(category="web")
def read_webpage(url: str, max_chars: int = 12000) -> dict:
    """Download a web page and return its readable text (for summarising articles, docs, recipes…).

    Args:
        url: Full http(s) URL.
        max_chars: Maximum characters of text to return.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolError(f"Couldn't load {url}: {exc}") from exc
    if "html" not in r.headers.get("content-type", "html"):
        return {"url": str(r.url), "text": r.text[:max_chars]}
    title, text = html_to_text(r.text)
    return {"url": str(r.url), "title": title, "text": text[: max(500, min(max_chars, 40000))]}


@tool(category="web", summary=lambda a: f"Open {a.get('url')}")
def open_website(url: str) -> str:
    """Open a website in the default browser.

    Args:
        url: URL or domain such as "github.com".
    """
    if not re.match(r"^[a-z][a-z0-9+.-]*:", url):
        url = "https://" + url
    if not url.startswith(("http://", "https://")):
        raise ToolError("Only http(s) links can be opened.")
    open_target(url)
    return f"Opened {url}."


@tool(category="web", summary=lambda a: f"Google “{a.get('query')}”")
def search_in_browser(query: str, site: Literal["google", "youtube", "maps", "images", "amazon", "github"] = "google") -> str:
    """Open a search results page in the browser (when the user wants to *see* results).

    Args:
        query: What to search for.
        site: Which site to search.
    """
    q = urllib.parse.quote_plus(query)
    url = {
        "google": f"https://www.google.com/search?q={q}",
        "youtube": f"https://www.youtube.com/results?search_query={q}",
        "maps": f"https://www.google.com/maps/search/{q}",
        "images": f"https://www.google.com/search?tbm=isch&q={q}",
        "amazon": f"https://www.amazon.com/s?k={q}",
        "github": f"https://github.com/search?q={q}",
    }[site]
    open_target(url)
    return f"Opened {site} results for “{query}”."


@tool(category="media", summary=lambda a: f"Play “{a.get('query')}” on YouTube")
def play_youtube(query: str) -> str:
    """Find a YouTube video and start playing it in the browser.

    Args:
        query: Song, video or topic to play.
    """
    url = None
    try:
        r = httpx.get("https://www.youtube.com/results", params={"search_query": query},
                      headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=10)
        m = re.search(r'"videoId":"([\w-]{11})"', r.text)
        if m:
            url = f"https://www.youtube.com/watch?v={m.group(1)}"
    except httpx.HTTPError:
        pass
    open_target(url or f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}")
    return f"Playing “{query}” on YouTube." if url else f"Opened YouTube results for “{query}”."


@tool(category="media", summary=lambda a: f"Spotify: {a.get('query')}")
def play_spotify(query: str) -> str:
    """Open Spotify on a search for a song, artist, album or playlist.

    Args:
        query: What to play.
    """
    open_target(f"spotify:search:{urllib.parse.quote(query)}")
    return f"Opened Spotify search for “{query}”. Press play or ask me to use media controls."


_WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
        51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
        66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
        77: "snow grains", 80: "rain showers", 81: "heavy showers", 82: "violent showers", 85: "snow showers",
        86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm"}


def _locate(location: str) -> tuple[float, float, str]:
    if not location:
        try:
            j = httpx.get("https://ipapi.co/json/", timeout=6, headers={"User-Agent": UA}).json()
            return float(j["latitude"]), float(j["longitude"]), f"{j.get('city')}, {j.get('country_name')}"
        except Exception as exc:
            raise ToolError("Couldn't detect your location; please name a city.") from exc
    j = _get_json("https://geocoding-api.open-meteo.com/v1/search", {"name": location, "count": 1}, 8)
    if not j.get("results"):
        raise ToolError(f"Unknown place: {location}")
    g = j["results"][0]
    return g["latitude"], g["longitude"], f"{g['name']}, {g.get('country', '')}"


def _get_json(url: str, params: dict, timeout: float = 10) -> dict:
    """GET JSON, retrying once on a dropped connection or timeout (free APIs blip now and then)."""
    for attempt in range(2):
        try:
            return httpx.get(url, params=params, timeout=timeout, headers={"User-Agent": UA}).json()
        except httpx.TransportError as exc:
            if attempt:
                raise ToolError(f"The weather service didn't respond ({type(exc).__name__}); try again shortly.") from exc
            time.sleep(1.0)
    return {}


@tool(category="web")
def get_weather(location: str = "", days: int = 1) -> dict:
    """Current weather and forecast. Leave location empty to use the user's approximate location.

    Args:
        location: City or place name.
        days: Forecast days (1-7).
    """
    lat, lon, place = _locate(location)
    j = _get_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": max(1, min(days, 7)),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    })
    c = j["current"]
    d = j["daily"]
    return {
        "place": place,
        "now": f"{c['temperature_2m']}°C (feels {c['apparent_temperature']}°C), {_WMO.get(c['weather_code'], 'n/a')}, "
               f"humidity {c['relative_humidity_2m']}%, wind {c['wind_speed_10m']} km/h",
        "forecast": [
            {"date": d["time"][i], "summary": _WMO.get(d["weather_code"][i], "n/a"),
             "min": d["temperature_2m_min"][i], "max": d["temperature_2m_max"][i],
             "rain_chance": d["precipitation_probability_max"][i]}
            for i in range(len(d["time"]))
        ],
    }
