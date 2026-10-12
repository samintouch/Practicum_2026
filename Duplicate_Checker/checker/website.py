"""Visit each record's website and check whether it shows the address we have in REDCap."""
from __future__ import annotations

import queue
import re
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import lxml.html
import requests

from .address import FoundAddress, address_on_page, compare_addresses, find_addresses_in_text, parse_address
from .data import Record
from .llm import OllamaClient
from .matching import distinctive_name_words

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SUBPAGE_HINTS = ("contact", "location", "about", "find-us", "directions", "visit", "office")
MAX_SUBPAGES = 3


@dataclass
class PageData:
    url: str
    final_url: str = ""
    status_code: int | None = None
    error: str | None = None
    title: str = ""
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    via: str = "download"            # "download" (plain HTTP request) or "browser" (Playwright)

    @property
    def ok(self) -> bool:
        return self.error is None


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    url = raw.strip().split()[0].strip("<>\"'")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    host = urlparse(url).netloc
    return url if "." in host else None


def _extract(content: bytes, base_url: str) -> tuple[str, str, list[tuple[str, str]]]:
    doc = lxml.html.fromstring(content)
    extra: list[str] = []
    # schema.org address data that many sites embed for search engines
    for script in doc.xpath('//script[@type="application/ld+json"]'):
        for m in re.finditer(r'"streetAddress"\s*:\s*"([^"]+)"', script.text_content() or ""):
            tail = script.text_content()[m.end():m.end() + 300]
            city = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', tail)
            zip_code = re.search(r'"postalCode"\s*:\s*"([^"]+)"', tail)
            extra.append(f"{m.group(1)} | {city.group(1) if city else ''}, TX {zip_code.group(1) if zip_code else ''}")
    for bad in doc.xpath("//script|//style|//noscript|//svg|//template"):
        bad.drop_tree()
    title = " ".join((doc.findtext(".//title") or "").split())
    parts = [" ".join(t.split()) for t in doc.itertext()]
    text = " | ".join([p for p in parts if p] + extra)
    links = []
    for a in doc.xpath("//a[@href]"):
        href = urljoin(base_url, a.get("href"))
        links.append((href, " ".join(a.text_content().split())[:80]))
    return title, text, links


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


class BrowserRenderer:
    """Opens pages in a headless Chromium browser (Playwright) - for sites that block plain downloads
    or build their content with JavaScript. Playwright must stay on the thread that started it, while
    website checks run on several threads, so one background thread does all the browsing."""

    def __init__(self, timeout: int = 25):
        self.timeout = timeout
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self.error: Exception | None = None

    def render(self, url: str) -> tuple[int | None, str, str]:
        """Returns (HTTP status, final URL, page HTML)."""
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="browser", daemon=True)
                self._thread.start()
        if self.error:
            raise self.error
        result: Future = Future()
        self._queue.put((url, result))
        return result.result(timeout=self.timeout * 3)

    def close(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=15)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(user_agent=USER_AGENT, locale="en-US")
                while (item := self._queue.get()) is not None:
                    url, result = item
                    try:
                        result.set_result(self._open(context, url))
                    except Exception as exc:      # one bad page must not stop the browser
                        result.set_exception(exc)
                browser.close()
        except Exception as exc:                  # browser could not start (e.g. not installed)
            self.error = exc
            while (item := self._queue.get()) is not None:
                item[1].set_exception(exc)

    def _open(self, context, url: str) -> tuple[int | None, str, str]:
        page = context.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)   # let scripts fill in the page
            except Exception:
                pass
            return (resp.status if resp else None), page.url, page.content()
        finally:
            page.close()


class PageFetcher:
    """Downloads pages fresh on every run - nothing is saved to disk. Within one run a page is only
    fetched once, even when several records link to the same site.
    With a BrowserRenderer, pages can also be opened in a real browser (see get_rendered)."""

    def __init__(self, timeout: int = 20, browser: BrowserRenderer | None = None):
        self.timeout = timeout
        self.browser = browser
        self._local = threading.local()
        self._pages: dict[str, PageData] = {}
        self._lock = threading.Lock()

    def _remember(self, key: str, fetch) -> PageData:
        with self._lock:
            if key in self._pages:
                return self._pages[key]
        page = fetch()
        with self._lock:
            return self._pages.setdefault(key, page)

    def worth_rendering(self, page: PageData) -> bool:
        """A browser can get past bot blocks and slow sites, but not a dead domain, a missing page or a
        certificate for the wrong site (usually a wrong link)."""
        if self.browser is None or page.ok:
            return self.browser is not None
        err = page.error or ""
        return not (err.startswith("SSL") or err.startswith("could not connect")
                    or err.startswith("HTTP 404") or err.startswith("HTTP 410"))

    def get_rendered(self, url: str) -> PageData:
        """The page as a browser sees it."""
        return self._remember(f"browser|{url}", lambda: self._render(url))

    def _render(self, url: str) -> PageData:
        page = PageData(url=url, via="browser")
        try:
            status, final_url, html = self.browser.render(url)
            page.status_code, page.final_url = status, final_url
            if status and status >= 400:
                page.error = f"HTTP {status} (also in a browser)"
            else:
                page.title, page.text, page.links = _extract(html.encode("utf-8", "ignore"), final_url)
        except Exception as exc:
            page.error = f"browser could not open the page ({exc.__class__.__name__})"
        return page

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
            self._local.session = s
        return self._local.session

    def get(self, url: str) -> PageData:
        return self._remember(url, lambda: self._download(url))

    def _download(self, url: str) -> PageData:
        page = PageData(url=url)
        try:
            resp = self._session().get(url, timeout=self.timeout, allow_redirects=True)
        except requests.exceptions.SSLError:
            page.error = "SSL certificate error - the site's certificate does not match this domain"
            return page
        except requests.exceptions.Timeout:
            page.error = f"timed out after {self.timeout}s"
            return page
        except requests.exceptions.ConnectionError:
            page.error = "could not connect - site down or domain no longer exists"
            return page
        except requests.RequestException as exc:
            page.error = f"request failed ({exc.__class__.__name__})"
            return page
        page.status_code = resp.status_code
        page.final_url = resp.url
        if resp.status_code >= 400:
            hint = " (site may block automated access)" if resp.status_code in (401, 403, 429) else ""
            page.error = f"HTTP {resp.status_code}{hint}"
            return page
        if "html" not in resp.headers.get("Content-Type", "html").lower():
            page.error = f"not a web page ({resp.headers.get('Content-Type')})"
            return page
        try:
            page.title, page.text, page.links = _extract(resp.content, resp.url)
        except (ValueError, lxml.etree.ParserError):
            page.error = "page could not be read"
        return page


@dataclass
class WebsiteCheck:
    record_id: str
    url: str | None
    status: str      # MATCH, SUITE_DIFFERS, DIFFERENT, OTHER_LOCATION, NOT_FOUND, UNREACHABLE, NO_WEBSITE, INVALID_URL
    detail: str = ""
    final_url: str = ""
    page_title: str = ""
    pages_checked: list[str] = field(default_factory=list)
    matched_text: str = ""
    found_addresses: list[str] = field(default_factory=list)
    suggested_address: str = ""
    name_on_page: bool | None = None
    found_by_ai: bool = False
    via: str = ""                                        # "download" or "browser"
    page_text: str = field(default="", repr=False)      # kept for cross-checks, not reported

    def summary(self) -> str:
        parts = [self.status]
        if self.detail:
            parts.append(self.detail)
        if self.found_addresses and self.status not in ("MATCH",):
            parts.append("addresses on site: " + "; ".join(self.found_addresses[:4]))
        if self.name_on_page is False:
            parts.append("organization name not found on the site")
        return " - ".join(parts)


def _same_site(a: str, b: str) -> bool:
    strip = lambda u: urlparse(u).netloc.lower().removeprefix("www.")
    return strip(a) == strip(b)


def _subpage_urls(page: PageData) -> list[str]:
    urls: list[str] = []
    for href, text in page.links:
        target = f"{href} {text}".lower()
        if (href.startswith("http") and _same_site(href, page.final_url or page.url)
                and any(h in target for h in SUBPAGE_HINTS) and href.split("#")[0] not in urls
                and href.split("#")[0].rstrip("/") != (page.final_url or page.url).rstrip("/")):
            urls.append(href.split("#")[0])
        if len(urls) >= MAX_SUBPAGES:
            break
    return urls


def _format_found(found: FoundAddress) -> str:
    return f"{found.snippet}{', ' + found.zip if found.zip and found.zip not in found.snippet else ''}"


def _local(record: Record, found: list[FoundAddress]) -> list[FoundAddress]:
    """Addresses on the site that are in the record's zip code or city - candidates for a correction.
    Anything else is more likely a head office or another branch."""
    same_zip = [f for f in found if record.zip and f.zip == record.zip]
    if same_zip:
        return same_zip
    if record.city:
        return [f for f in found if re.search(r"\b" + re.escape(record.city) + r"\b", f.context, re.I)]
    return []


def check_website(record: Record, fetcher: PageFetcher, llm: OllamaClient | None = None) -> WebsiteCheck:
    url = normalize_url(record.website)
    if record.website is None:
        return WebsiteCheck(record.record_id, None, "NO_WEBSITE", "no website in REDCap")
    if url is None:
        return WebsiteCheck(record.record_id, record.website, "INVALID_URL", "website field is not a valid URL")

    home = fetcher.get(url)
    if not home.ok and fetcher.browser and fetcher.worth_rendering(home):
        rendered = fetcher.get_rendered(url)          # blocked or timed out - try a real browser
        if rendered.ok:
            home = rendered
    if not home.ok:
        return WebsiteCheck(record.record_id, url, "UNREACHABLE", home.error or "", final_url=home.final_url)

    can_retry_in_browser = fetcher.browser is not None and home.via != "browser"
    check = _analyse(record, fetcher, url, home, None if can_retry_in_browser else llm)
    if check.status == "NOT_FOUND" and can_retry_in_browser:
        # the address may only appear once the page's scripts have run
        rendered = fetcher.get_rendered(url)
        retry = _analyse(record, fetcher, url, rendered, llm) if rendered.ok else None
        if retry is not None and retry.status != "NOT_FOUND":
            check = retry
        elif llm is not None:
            check = _analyse(record, fetcher, url, home, llm)
    if check.via == "browser":
        check.detail += " (read with a browser)"
    return check


def _analyse(record: Record, fetcher: PageFetcher, url: str, home: PageData,
             llm: OllamaClient | None) -> WebsiteCheck:
    """Compare the record's address with what the site shows (home page plus contact/location pages)."""
    check = WebsiteCheck(record.record_id, url, "NOT_FOUND", final_url=home.final_url, page_title=home.title,
                         via=home.via)
    pages = [home]
    addr = record.address
    hit = address_on_page(addr, home.text, record.city)
    if hit is None:
        for sub_url in _subpage_urls(home):
            sub = fetcher.get(sub_url)
            if sub.ok:
                pages.append(sub)
                hit = address_on_page(addr, sub.text, record.city)
                if hit:
                    break

    all_text = " | ".join(p.text for p in pages)
    check.page_text = all_text
    check.pages_checked = [p.final_url or p.url for p in pages]
    words = distinctive_name_words(record.name)
    if words:
        check.name_on_page = any(re.search(rf"\b{re.escape(w)}\b", all_text, re.I) for w in words)

    if hit:
        cmp, snippet = hit
        check.matched_text = snippet
        if cmp.level == "SAME":
            check.status, check.detail = "MATCH", "website shows the same address"
        else:
            check.status, check.detail = "SUITE_DIFFERS", f"website shows the same building, {cmp.detail}"
            check.suggested_address = snippet
        return check

    found = find_addresses_in_text(all_text, record.city)
    if not found and llm is not None:
        found = _ai_extract(record, all_text, llm)
        check.found_by_ai = bool(found)
    check.found_addresses = [_format_found(f) for f in found]
    if not found:
        check.status, check.detail = "NOT_FOUND", "no street address found on the website"
        return check

    # a site may show the same address in a different format the window logic missed
    for f in found:
        if compare_addresses(addr, f.parsed).level == "SAME":
            check.status, check.detail, check.matched_text = "MATCH", "website shows the same address", f.snippet
            return check

    local = _local(record, found)
    if len(local) == 1:
        check.status = "DIFFERENT"
        check.suggested_address = _format_found(local[0])
        check.detail = (f"website shows a different address in {record.city or 'the same zip'}"
                        + (" (found by AI)" if check.found_by_ai else ""))
    elif local:
        check.status = "DIFFERENT"
        check.detail = f"website lists {len(local)} other addresses in {record.city or 'this zip'} - none match; verify which is correct"
    else:
        check.status = "OTHER_LOCATION"
        check.detail = ("website only shows address(es) in another city/zip - probably a main office or other "
                        "branch; record address not confirmed")
    return check


def _ai_extract(record: Record, text: str, llm: OllamaClient) -> list[FoundAddress]:
    excerpt = text if len(text) <= 5000 else text[:2500] + " ... " + text[-2500:]
    result = llm.extract_address(record.name or "", excerpt)
    if not result or not str(result.get("street", "")).strip():
        return []
    street = str(result["street"]).strip()
    parsed = parse_address(street)
    # only trust it if the house number and a street word really appear on the page
    if (not parsed or not parsed.number or not re.search(rf"\b{re.escape(parsed.number)}\b", text)
            or not any(re.search(rf"\b{re.escape(t)}", text, re.I) for t in parsed.street_tokens if len(t) >= 4)):
        return []
    zip_code = re.sub(r"\D", "", str(result.get("zip", "")))[:5] or None
    city = str(result.get("city", "")).strip()
    snippet = f"{street}, {city}" if city else street
    return [FoundAddress(snippet=snippet, parsed=parsed, zip=zip_code)]
