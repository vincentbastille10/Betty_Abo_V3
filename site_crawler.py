# site_crawler.py
from __future__ import annotations

import re
import time
import logging
from typing import Dict, List, Set, Tuple
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; BettySiteCrawler/1.0; +https://spectramedia.online)"
)

SKIP_EXTENSIONS = {
    ".jpg",".jpeg",".png",".gif",".webp",".svg",".ico",
    ".pdf",".zip",".rar",".7z",
    ".mp4",".mov",".avi",".mp3",".wav",
    ".css",".js",".map",
    ".woff",".woff2",".ttf",".eot",
}

MAX_TEXT_LENGTH = 12000


def _normalize_url(u: str) -> str:
    u = (u or "").strip()
    u, _ = urldefrag(u)
    return u


def _is_same_domain(base: str, candidate: str) -> bool:
    try:
        b = urlparse(base)
        c = urlparse(candidate)

        if not c.netloc:
            return True

        return c.netloc.lower() == b.netloc.lower()

    except Exception:
        return False


def _should_skip(url: str) -> bool:

    parsed = urlparse(url)
    path = parsed.path.lower()

    # ignore fichiers
    for ext in SKIP_EXTENSIONS:
        if path.endswith(ext):
            return True

    # ignore pages techniques
    if any(x in path for x in [
        "login","signin","signup",
        "account","cart","checkout",
        "privacy","politique",
        "cookies","mentions","legal",
        "terms","cgu"
    ]):
        return True

    return False


def _extract_text_and_links(html: str, base_url: str) -> Tuple[str, str, List[str]]:

    soup = BeautifulSoup(html, "html.parser")

    # supprime zones non utiles
    for tag in soup([
        "script","style","noscript",
        "svg","canvas","header","footer","nav"
    ]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    text = soup.get_text(separator="\n")

    text = re.sub(r"[ \t]+"," ",text)
    text = re.sub(r"\n{3,}","\n\n",text)
    text = text.strip()

    # ignore pages trop pauvres
    if len(text) < 120:
        return title,"",[]

    # limite taille
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]

    links = []

    for a in soup.find_all("a",href=True):

        href = a.get("href","").strip()

        if not href:
            continue

        abs_url = urljoin(base_url,href)
        abs_url = _normalize_url(abs_url)

        if not _is_same_domain(base_url,abs_url):
            continue

        if _should_skip(abs_url):
            continue

        parsed = urlparse(abs_url)

        if parsed.scheme not in ("http","https"):
            continue

        links.append(abs_url)

    links = list(set(links))

    return title,text,links


def crawl_site(
    base_url: str,
    *,
    max_pages: int = 60,
    timeout_s: int = 12,
    delay_s: float = 0.15,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Dict:

    base_url = _normalize_url(base_url)

    if not base_url.startswith(("http://","https://")):
        base_url = "https://" + base_url

    seen: Set[str] = set()
    queue: List[str] = [base_url]
    pages: List[Dict] = []

    session = requests.Session()
    session.headers.update({"User-Agent":user_agent})

    while queue and len(pages) < max_pages:

        url = queue.pop(0)
        url = _normalize_url(url)

        if not url:
            continue

        if url in seen:
            continue

        seen.add(url)

        if _should_skip(url):
            continue

        if not _is_same_domain(base_url,url):
            continue

        try:

            logger.info("Crawl: %s",url)

            r = session.get(
                url,
                timeout=timeout_s,
                allow_redirects=True
            )

            if r.status_code >= 400:
                continue

            content_type = (r.headers.get("Content-Type") or "").lower()

            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                continue

            title,text,links = _extract_text_and_links(r.text,r.url)

            if not text:
                continue

            pages.append({
                "url":_normalize_url(r.url),
                "title":title,
                "text":text,
                "links":links
            })

            for l in links:
                if l not in seen and l not in queue:
                    queue.append(l)

            if delay_s > 0:
                time.sleep(delay_s)

        except requests.RequestException as e:

            logger.warning("Crawler request error %s : %s",url,e)
            continue

        except Exception as e:

            logger.warning("Crawler parse error %s : %s",url,e)
            continue

    return {
        "base_url":base_url,
        "crawled_at":int(time.time()),
        "pages":pages
    }
