"""Fetch a facility's home page plus its contact/about/location pages."""

import queue
import re
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from urllib.parse import urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup

from .normalize import host

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30

# Links worth following from the home page, best first.
_LINK_HINTS = ["contact", "location", "find-us", "directions", "visit", "about", "office", "hours"]
# A page with less visible text than this is probably rendered by JavaScript.
_MIN_TEXT_CHARS = 300


@dataclass
class Page:
    url: str
    html: str

    def __post_init__(self):
        self.html = re.sub("[​-‍⁠﻿]", "", self.html)   # zero-width characters


class FetchError(Exception):
    pass


def _get(url):
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    if "html" not in resp.headers.get("Content-Type", "html"):
        raise FetchError(f"{url} is not an HTML page")
    return resp.url, resp.text


class Browser:
    """Headless Chromium (Playwright), for sites that refuse plain
    requests (bot protection) and, with render_js, for sites built with
    JavaScript. Playwright objects must stay on the thread that made them,
    so one background thread owns the browser and pages are fetched
    through a queue; any number of worker threads can share it. The
    browser only starts the first time it's needed."""

    def __init__(self, render_js=False):
        self.render_js = render_js
        self._jobs = queue.Queue()
        self._thread = None
        self._start_lock = threading.Lock()

    def _run(self, ready):
        from playwright.sync_api import sync_playwright
        try:
            pw = sync_playwright().start()
            # Playwright's headless Chromium, with its own user agent: setting
            # a different one trips some sites' bot protection
            browser = pw.chromium.launch()
            ready.set_result(True)
        except Exception as exc:
            ready.set_exception(exc)
            return
        while True:
            job = self._jobs.get()
            if job is None:
                break
            url, want_visible_text, result = job
            try:
                page = browser.new_page()
                try:
                    page.goto(url, timeout=TIMEOUT * 1000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)    # let scripts fill in the page
                    # inner_text leaves out whatever the page's CSS hides
                    content = page.inner_text("body") if want_visible_text else page.content()
                    result.set_result((page.url, content))
                finally:
                    page.close()
            except Exception as exc:
                result.set_exception(exc)
        browser.close()
        pw.stop()

    def _submit(self, url, want_visible_text):
        with self._start_lock:
            if self._thread is None:
                ready = Future()
                self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
                self._thread.start()
                if not ready.result(timeout=120):
                    raise FetchError("no browser could be started")
        result = Future()
        self._jobs.put((url, want_visible_text, result))
        return result.result(timeout=TIMEOUT * 3)

    def get(self, url):
        """(final url, html) of the page as a browser renders it."""
        return self._submit(url, False)

    def visible_text(self, url):
        """Only the text a visitor can see; hidden template blocks are left out."""
        return self._submit(url, True)[1]

    def close(self):
        if self._thread is not None:
            self._jobs.put(None)
            self._thread.join(timeout=30)


def _visible_text_length(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return len(soup.get_text(" ", strip=True))


def fetch_page(url, browser=None):
    """Plain request first. If the site refuses it (connection dropped,
    403, 429, 5xx...), retry the page in the browser. A 404/410 is a real
    missing page and is not retried."""
    try:
        final_url, html = _get(url)
        if browser and browser.render_js and _visible_text_length(html) < _MIN_TEXT_CHARS:
            final_url, html = browser.get(url)
        return Page(final_url, html)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if browser and status not in (404, 410):
            try:
                final_url, html = browser.get(url)
                return Page(final_url, html)
            except Exception:
                pass
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc


def _candidate_links(page):
    soup = BeautifulSoup(page.html, "html.parser")
    site = host(page.url)
    scored = {}
    for a in soup.find_all("a", href=True):
        url = urldefrag(urljoin(page.url, a["href"]))[0]
        if urlparse(url).scheme not in ("http", "https") or host(url) != site:
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|docx?|xlsx?|zip)$", url, re.I):
            continue
        haystack = f"{url} {a.get_text(' ', strip=True)}".lower()
        for rank, hint in enumerate(_LINK_HINTS):
            if hint in haystack:
                scored[url] = min(scored.get(url, rank), rank)
                break
    return sorted(scored, key=scored.get)


_ROOT_PATHS = {"", "/", "/home", "/index.html", "/index.htm", "/index.php", "/en", "/en/"}


def is_deep_link(url):
    """True for a link to one page of a bigger site, like a chain's
    /locations/allen-tx page. Other pages on that site describe other
    locations or the head office, so only the linked page is read."""
    return urlparse(url).path.rstrip("/").lower() not in _ROOT_PATHS


def fetch_site(website, max_pages=6, browser=None):
    """Return the list of pages fetched. Raises FetchError if even the first
    page can't be loaded."""
    url = website.strip()
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    if is_deep_link(url):
        max_pages = 1
    try:
        home = fetch_page(url, browser)
    except FetchError:
        if url.startswith("https://"):
            home = fetch_page("http://" + url[len("https://"):], browser)
        else:
            raise

    pages, seen = [home], {home.url.rstrip("/")}
    for link in _candidate_links(home):
        if len(pages) >= max_pages:
            break
        if link.rstrip("/") in seen:
            continue
        seen.add(link.rstrip("/"))
        try:
            pages.append(fetch_page(link, browser))
        except FetchError:
            continue
    return pages
