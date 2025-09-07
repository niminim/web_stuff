# -*- coding: utf-8 -*-
"""
Article text enricher for a nested `res` structure.

- Adds `item['text']` to each article dict in-place.
- Fast path: concurrent requests + trafilatura/newspaper3k/BS4 fallback.
- Slow path (optional): single serial Playwright pass for leftovers (consent, lazy load, JS sites).
- Handles Google News RSS wrapping; skips aggregator stubs (Biztoc/RapidAPI, etc.).
- Retries with exponential backoff, randomizes UA, optional proxy hook.

Usage:
    # 1) Enrich in-place
    res = enrich_res_with_text(res, max_workers=16, use_playwright=True, print_text=False)

    # 2) Access text per article
    for provider, pdata in res.get("providers", {}).items():
        for item in pdata.get("items", []):
            print(provider, "=>", item.get("title"), "=>", len(item.get("text","")), "chars")

    # (Optional) Show a specific row (flattened order)
    preview_article(res, row_number=3, preview_chars=400)
"""

from __future__ import annotations

import html
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlparse, parse_qs

import requests

# ---------- Optional dependencies ----------
try:
    import trafilatura  # type: ignore
    HAVE_TRAFI = True
except Exception:
    HAVE_TRAFI = False

try:
    from newspaper import Article  # type: ignore
    HAVE_NEWS = True
except Exception:
    HAVE_NEWS = False

try:
    from bs4 import BeautifulSoup  # type: ignore
    HAVE_BS4 = True
except Exception:
    HAVE_BS4 = False

# ---------- Requests session / headers ----------
UA_POOL = [
    # Rotate a few realistic desktop UAs (add more if needed)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

def _base_headers():
    return {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://news.google.com/",
    }

SESSION = requests.Session()
SESSION.headers.update(_base_headers())
DEFAULT_TIMEOUT = 20

# Hook for proxy rotation if you use it (return dict like {"http": "...", "https": "..."})
def _get_proxies() -> Optional[Dict[str, str]]:
    return None  # replace with your proxy selection if needed


# ---------- Small utils ----------
def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except Exception:
        return ""

def _is_google_news_rss(url: str) -> bool:
    if not url:
        return False
    u = urlparse(url)
    return ("news.google.com" in (u.netloc or "").lower()) and ("/rss/" in (u.path or "").lower())

def _unwrap_google_url_wrapper(gurl: str) -> Optional[str]:
    """Handle links like https://www.google.com/url?url=<REAL> or ...&q=<REAL>"""
    try:
        q = parse_qs(urlsplit(gurl).query)
        for key in ("url", "q"):
            vals = q.get(key)
            if vals and vals[0].startswith("http"):
                return vals[0]
    except Exception:
        pass
    return None

def _looks_external(href: str) -> bool:
    if not href or not href.startswith("http"):
        return False
    host = _host(href)
    return not any(bad in host for bad in ("google.com", "news.google.com", "googleusercontent.com", "gstatic.com"))

def _is_obvious_aggregator(url: str) -> bool:
    h = _host(url)
    if not h:
        return False
    # Known stubs / API frontends that don't host full articles
    if "rapidapi.com" in h:
        return True
    if "biztoc.com" in h and "/x/" in url:
        return True
    return False

def _sleep_jitter(a=0.05, b=0.25):
    time.sleep(random.uniform(a, b))


# ---------- Google News resolver ----------
def resolve_gnews_redirect(url: str) -> str:
    """
    Resolve Google News RSS click-through to the publisher page.
    - Try HEAD/GET
    - Parse HTML for meta refresh or first external link
    - Unwrap https://www.google.com/url?... wrappers
    """
    if not _is_google_news_rss(url):
        return url

    # 1) HEAD follow redirects
    try:
        r = SESSION.head(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, proxies=_get_proxies())
        if r.url and not _is_google_news_rss(r.url):
            return r.url
    except requests.RequestException:
        pass

    # 2) GET to inspect body
    html_text = ""
    try:
        r = SESSION.get(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, proxies=_get_proxies())
        if r.url and not _is_google_news_rss(r.url):
            return r.url
        html_text = r.text or ""
    except requests.RequestException:
        pass

    if not html_text:
        try:
            r2 = SESSION.get(url, allow_redirects=False, timeout=DEFAULT_TIMEOUT, proxies=_get_proxies())
            html_text = r2.text or ""
        except requests.RequestException:
            pass

    if not html_text:
        return url

    # meta refresh
    m = re.search(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\'<>]*url=([^"\'<>]+)', html_text, flags=re.I)
    if m:
        tgt = html.unescape(m.group(1))
        if _looks_external(tgt):
            return tgt

    # google.com/url wrappers
    for href in re.findall(r'href=["\']([^"\']+)["\']', html_text, flags=re.I):
        hdec = html.unescape(href)
        if hdec.startswith("https://www.google.com/url"):
            real = _unwrap_google_url_wrapper(hdec)
            if real and _looks_external(real):
                return real

    # first external <a>
    if HAVE_BS4:
        try:
            soup = BeautifulSoup(html_text, "lxml")
            for a in soup.find_all("a", href=True):
                hdec = html.unescape(a["href"])
                if hdec.startswith("https://www.google.com/url"):
                    real = _unwrap_google_url_wrapper(hdec)
                    if real and _looks_external(real):
                        return real
                if _looks_external(hdec):
                    return hdec
        except Exception:
            pass

    return url


# ---------- Requests fetch with retries ----------
def fetch_html_requests(url: str, max_retries: int = 3) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (html_text, final_url). Uses GET with redirects + UA rotation + backoff.
    """
    attempt = 0
    last_exc = None
    final_url = None
    while attempt < max_retries:
        attempt += 1
        try:
            # Slight UA rotation
            SESSION.headers["User-Agent"] = random.choice(UA_POOL)
            resp = SESSION.get(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, proxies=_get_proxies())
            final_url = resp.url or url
            if resp.status_code == 200 and resp.text and len(resp.text) > 200:
                return resp.text, final_url
            # Retry on soft failures / anti-bot codes
            if resp.status_code in (403, 429, 503):
                time.sleep(0.5 * attempt + random.random() * 0.2)
            else:
                break
        except requests.RequestException as e:
            last_exc = e
            time.sleep(0.5 * attempt + random.random() * 0.2)
    return None, final_url or url


# ---------- Extractors (fast path) ----------
def extract_text_from_html(url: str, html_text: Optional[str]) -> str:
    """
    Try extractors in order: trafilatura -> newspaper3k -> BS4 paragraph sweep
    Return empty string if nothing good is found.
    """
    # Trafilatura
    if HAVE_TRAFI:
        try:
            if html_text:
                text = trafilatura.extract(html_text, url=url, include_tables=False, favor_precision=True)
            else:
                fetched = trafilatura.fetch_url(url)
                text = trafilatura.extract(fetched, url=url, include_tables=False, favor_precision=True) if fetched else None
            if text and len(text.strip()) > 200:
                return text.strip()
        except Exception:
            pass

    # Newspaper3k
    if HAVE_NEWS:
        try:
            art = Article(url)
            if html_text:
                art.set_html(html_text)
            else:
                art.download()
            art.parse()
            if art.text and len(art.text.strip()) > 200:
                return art.text.strip()
        except Exception:
            pass

    # BS4 paragraph sweep
    if HAVE_BS4 and html_text:
        try:
            soup = BeautifulSoup(html_text, "lxml")
            parts = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            out = "\n".join([p for p in parts if p and len(p) > 1])
            if len(out.strip()) > 200:
                return out.strip()
        except Exception:
            pass

    return ""


# ---------- Domain-aware DOM extraction (Playwright) ----------
DOMAIN_SELECTORS: Dict[str, List[str]] = {
    "blogs.nvidia.com": ["article", ".entry-content", "main .entry-content", "[itemprop='articleBody']"],
    "developer.nvidia.com": ["article", ".entry-content", "main .entry-content", "[itemprop='articleBody']"],
    "newsroom.cisco.com": ["article", ".article-body", ".cisco-article__body", "[itemprop='articleBody']"],
    "reuters.com": ["article", "[data-testid='ArticleBody']", "[data-testid='Body']", "[itemprop='articleBody']"],
    "investors.com": ["article", "#article-content", ".single-article__content", "[itemprop='articleBody']"],
    "morningstar.com": ["article", "[data-test='article-body']", ".mdc-article", "[itemprop='articleBody']"],
    "sherwood.news": ["article", ".entry-content", ".article-content", "[itemprop='articleBody']"],
    "marketwatch.com": ["article", "div.article__content", "[itemprop='articleBody']"],
}

GENERIC_SELECTORS = [
    "article", "main", ".article-content", ".entry-content", ".post-content",
    ".c-article-content", "[itemprop='articleBody']", "#content"
]

def _extract_dom_text_page(page, domain: str) -> str:
    selectors = []
    for key, sels in DOMAIN_SELECTORS.items():
        if key in domain:
            selectors.extend(sels)
    selectors.extend(GENERIC_SELECTORS)

    # Preferred selectors
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                txt = loc.first.inner_text(timeout=1500).strip()
                if len(txt) > 200:
                    return txt
        except Exception:
            continue

    # JSON-LD articleBody
    try:
        scripts = page.locator("script[type='application/ld+json']")
        for i in range(min(scripts.count(), 10)):
            try:
                raw = scripts.nth(i).inner_text(timeout=2000)
                data = json.loads(raw)
                if isinstance(data, dict):
                    body = data.get("articleBody") or data.get("description")
                    if isinstance(body, str) and len(body.strip()) > 200:
                        return body.strip()
                elif isinstance(data, list):
                    for obj in data:
                        if isinstance(obj, dict):
                            body = obj.get("articleBody") or obj.get("description")
                            if isinstance(body, str) and len(body.strip()) > 200:
                                return body.strip()
            except Exception:
                continue
    except Exception:
        pass

    # Paragraph sweep
    try:
        ps = page.locator("p")
        n = min(ps.count(), 180)
        buf = []
        for i in range(n):
            try:
                t = ps.nth(i).inner_text(timeout=250).strip()
                if t and len(t) > 1:
                    buf.append(t)
            except Exception:
                pass
        if buf:
            out = "\n".join(buf)
            if len(out.strip()) > 200:
                return out.strip()
    except Exception:
        pass

    return ""


def _click_consent_and_expand(page) -> None:
    """Try clicking common consent/continue buttons in page & frames."""
    sels = [
        "button[name='agree']", "#agree", "button#agree", "button[value='agree']",
        "button:has-text('Agree')", "button:has-text('I agree')", "button:has-text('Accept')",
        "button:has-text('Accept all')", "button:has-text('Continue')", "a:has-text('Continue')",
        "a:has-text('Read more')", "button:has-text('Read more')", "button:has-text('Show more')",
        "[aria-label='Accept all']",
    ]
    try:
        for fr in [page.main_frame] + [f for f in page.frames if f is not page.main_frame]:
            for sel in sels:
                try:
                    loc = fr.locator(sel)
                    if loc.count() > 0:
                        loc.first.click(timeout=1200)
                        page.wait_for_timeout(300)
                except Exception:
                    continue
    except Exception:
        pass

def _scroll_page(page, steps=12, px=1400, wait=180):
    try:
        for _ in range(steps):
            page.mouse.wheel(0, px)
            page.wait_for_timeout(wait)
    except Exception:
        pass


# ---------- Playwright fallback (serial only) ----------
def playwright_extract_many(urls: List[str]) -> Dict[str, Tuple[str, str]]:
    """
    For a list of URLs, return a mapping:
        url_in -> (final_url, text)
    Serial, single browser context to avoid thread/greenlet issues.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {}

    out: Dict[str, Tuple[str, str]] = {}
    def is_consent_host(u: str) -> bool:
        u = (u or "").lower()
        return ("consent.yahoo.com" in u) or ("guce.yahoo.com" in u) or ("guce" in u)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random.choice(UA_POOL),
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
            locale="en-US",
        )
        page = context.new_page()

        for url in urls:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=35000)
                if is_consent_host(page.url):
                    _click_consent_and_expand(page)
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    page.wait_for_timeout(400)

                _click_consent_and_expand(page)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass

                _scroll_page(page, steps=14, px=1500, wait=200)
                _click_consent_and_expand(page)
                page.wait_for_timeout(400)

                final_url = page.url
                # Try DOM selectors first
                dom_text = _extract_dom_text_page(page, _host(final_url))

                # As second chance, run trafilatura on rendered HTML
                html_rendered = page.content()
                extracted = dom_text
                if (not extracted or len(extracted) < 200) and HAVE_TRAFI:
                    try:
                        t2 = trafilatura.extract(html_rendered, url=final_url, include_tables=False, favor_precision=True)
                        if t2 and len(t2.strip()) > 200:
                            extracted = t2.strip()
                    except Exception:
                        pass

                out[url] = (final_url, extracted or "")
            except Exception:
                out[url] = (url, "")
        browser.close()

    return out


# ---------- Flatten helpers ----------
@dataclass
class FlatItem:
    provider: str
    idx_in_provider: int
    item: Dict

def flatten_items(res: Dict) -> List[FlatItem]:
    flat: List[FlatItem] = []
    providers = (res or {}).get("providers", {}) or {}
    for prov, pdata in providers.items():
        items = (pdata or {}).get("items", []) or []
        for i, it in enumerate(items):
            flat.append(FlatItem(prov, i, it))
    return flat


# ---------- Main enricher ----------
def enrich_res_with_text(res: Dict,
                         max_workers: int = 16,
                         use_playwright: bool = True,
                         print_text: bool = False) -> Dict:
    """
    Enrich `res` in-place: add 'text' key to each article item.
    Returns the same dict (for convenience).
    """
    flat = flatten_items(res)
    if not flat:
        return res

    # 1) Build a worklist with normalized target URLs
    work: List[Tuple[str, FlatItem]] = []  # (normalized_url, flat_item)
    seen_norm: Dict[str, str] = {}  # normalized_url -> original_url_for_info
    for f in flat:
        url = f.item.get("url") or ""
        if not url:
            f.item["text"] = ""
            continue

        # unwrap Google News to publisher URL
        norm = resolve_gnews_redirect(url) if _is_google_news_rss(url) else url

        # skip obvious aggregator stubs (leave text empty)
        if _is_obvious_aggregator(norm):
            f.item["text"] = ""
            continue

        # Deduplicate by normalized URL
        if norm not in seen_norm:
            seen_norm[norm] = url
            work.append((norm, f))
        else:
            work.append((norm, f))  # will reuse cache later

    # 2) Requests phase (concurrent) + extractors
    cache_text: Dict[str, Tuple[str, str]] = {}  # norm_url -> (final_url, text)
    def worker(norm_url: str) -> Tuple[str, Tuple[str, str]]:
        _sleep_jitter()
        html_text, final_url = fetch_html_requests(norm_url)
        text = extract_text_from_html(final_url or norm_url, html_text)
        return norm_url, (final_url or norm_url, text)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(worker, norm): norm for norm, _ in work}
        for fut in as_completed(futures):
            norm = futures[fut]
            try:
                norm_back, (final_url, text) = fut.result()
                cache_text[norm_back] = (final_url, text)
            except Exception:
                cache_text[norm] = (norm, "")

    # 3) Identify leftovers for Playwright (no text yet)
    leftovers: List[str] = []
    for norm_url, _ in work:
        final_url, text = cache_text.get(norm_url, (norm_url, ""))
        if not text or len(text.strip()) < 200:
            leftovers.append(norm_url)

    # Filter leftovers to real sites (still skip aggregators)
    leftovers = [u for u in leftovers if not _is_obvious_aggregator(u)]

    # 4) Playwright fallback (serial)
    if use_playwright and leftovers:
        pw_out = playwright_extract_many(leftovers)  # norm -> (final_url, text)
        for norm_url, (final_url, text) in pw_out.items():
            prev_final, prev_text = cache_text.get(norm_url, (norm_url, ""))
            if not prev_text or len(prev_text) < 200:
                cache_text[norm_url] = (final_url, text)

    # 5) Write back to original items
    for norm_url, f in work:
        final_url, text = cache_text.get(norm_url, (norm_url, ""))
        # Update 'url' to resolved final (optional; keep if you want original)
        f.item["url"] = final_url or f.item.get("url")
        f.item["text"] = text or ""

        if print_text and text:
            print("=" * 60)
            print("Provider :", f.provider)
            print("Title    :", f.item.get("title"))
            print("URL      :", f.item.get("url"))
            print("Text     :", (text[:600] + "…") if len(text) > 600 else text)

    return res


# ---------- Convenience: preview a specific flattened row ----------
def preview_article(res: Dict, row_number: int = 1, preview_chars: int = 400):
    """
    Print a quick preview of a flattened article row (1-based index).
    """
    flat = flatten_items(res)
    if not flat:
        print("No items.")
        return
    if row_number < 1 or row_number > len(flat):
        print(f"Row out of range (1..{len(flat)}).")
        return
    f = flat[row_number - 1]
    item = f.item
    text = item.get("text", "")
    print("\n=== ARTICLE PREVIEW ===")
    print("Provider :", f.provider)
    print("Title    :", item.get("title"))
    print("URL      :", item.get("url"))
    print("Published:", item.get("published"))
    print("Source   :", item.get("source"))
    print("--- TEXT ---")
    print((text[:preview_chars] + "…") if len(text) > preview_chars else text)



########### Example

# 1) Enrich your existing `res` dict in place
res = enrich_res_with_text(res, max_workers=16, use_playwright=True, print_text=False)

# 2) Access the text of each item
for provider, pdata in res.get("providers", {}).items():
    for it in pdata.get("items", []):
        print(provider, "=>", it.get("title"), "=>", len(it.get("text","")), "chars")






res = enrich_res_with_text(res, max_workers=16, use_playwright=True) # dict_keys(['company', 'attempted', 'ok', 'errors', 'providers', 'merged'])


i = 20
provider = "google_news_rss" # google_news_rss, gnews, newsapi
entry = res['providers'][provider]['items'][i]

print(entry)
print(f"len: {len(entry['text'])} chars")

# Step 2: Access text
for provider, pdata in res.get("providers", {}).items():
    for item in pdata.get("items", []):
        title = item.get("title")
        url = item.get("url")
        text = item.get("text", "")
        print("===")
        print("Provider :", provider)
        print("Title    :", title)
        print("URL      :", url)
        print("Text     :", text[:400], "..." if len(text) > 400 else "")




# --- DataFrame helpers (drop these into the same file) ---
import pandas as pd

def res_to_dataframe(res: dict,
                     include_provider: bool = False,
                     drop_duplicates: bool = True,
                     drop_empty_text: bool = False,
                     text_preview_chars: int | None = None) -> pd.DataFrame:
    """
    Flatten `res` into a clean DataFrame with columns:
        ['title', 'url', 'published', 'source', 'text']  (+ 'provider' if requested)
    Options:
      - include_provider: add a 'provider' column.
      - drop_duplicates : drop duplicate rows by 'url' (keeps first).
      - drop_empty_text : remove rows where text is missing/empty.
      - text_preview_chars: if set (e.g. 240), also add 'text_preview' with a truncated version.
    """
    rows = []
    providers = (res or {}).get("providers", {}) or {}
    for prov, pdata in providers.items():
        for item in (pdata or {}).get("items", []) or []:
            row = {
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "published": item.get("published") or "",
                "source": item.get("source") or "",
                "text": item.get("text") or "",
            }
            if include_provider:
                row["provider"] = prov
            if text_preview_chars is not None:
                t = row["text"]
                row["text_preview"] = (t[:text_preview_chars] + "…") if (t and len(t) > text_preview_chars) else t
            rows.append(row)

    df = pd.DataFrame(rows, columns=(
        (["provider"] if include_provider else []) +
        ["title", "url", "published", "source", "text"] +
        (["text_preview"] if text_preview_chars is not None else [])
    ))

    if drop_duplicates and not df.empty:
        df = df.drop_duplicates(subset=["url"], keep="first").reset_index(drop=True)

    if drop_empty_text and not df.empty:
        df = df[df["text"].astype(str).str.strip().ne("")].reset_index(drop=True)

    return df





