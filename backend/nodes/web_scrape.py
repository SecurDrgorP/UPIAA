import re
import asyncio
import datetime
from datetime import timezone
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser

import httpx

from core.node_registry import register_action
from core.expressions import resolve_dotpath


# ══════════════════════════════════════════════════════════════════════════════
# SEMANTIC HTML PARSER
# ══════════════════════════════════════════════════════════════════════════════

class _SemanticParser(HTMLParser):
    """Converts raw HTML into typed semantic content blocks."""

    SKIP_TAGS = {"script", "style", "noscript", "meta", "link", "head"}

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.stack: list[str] = []

        self.blocks: list[dict] = []
        self.links: list[dict] = []
        self.images: list[dict] = []

        self._h_level = 0
        self._h_buf: list[str] = []
        self._anchor_n = 0

        self._p_buf: list[str] = []

        self._in_table = False
        self._t_headers: list[str] = []
        self._t_rows: list[list[str]] = []
        self._t_cur_row: list[str] = []
        self._t_cell_buf: list[str] = []
        self._t_in_th = False
        self._t_in_td = False

        self._in_list = False
        self._list_ordered = False
        self._list_items: list[str] = []
        self._li_buf: list[str] = []

        self._a_href: str | None = None
        self._a_buf: list[str] = []

    def _abs(self, href: str) -> str:
        return urljoin(self.base_url, href) if href else ""

    def _is_external(self, url: str) -> bool:
        try:
            return urlparse(url).netloc != urlparse(self.base_url).netloc
        except Exception:
            return False

    def _flush(self, buf: list[str]) -> str:
        return re.sub(r"\s+", " ", " ".join(buf)).strip()

    def handle_starttag(self, tag: str, attrs: list):
        self.stack.append(tag)
        ad = dict(attrs)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._h_level = int(tag[1])
            self._h_buf = []

        elif tag == "p":
            self._p_buf = []

        elif tag == "table":
            self._in_table = True
            self._t_headers, self._t_rows, self._t_cur_row = [], [], []

        elif tag == "tr":
            self._t_cur_row = []

        elif tag == "th":
            self._t_in_th = True
            self._t_cell_buf = []

        elif tag == "td":
            self._t_in_td = True
            self._t_cell_buf = []

        elif tag in ("ul", "ol"):
            self._in_list = True
            self._list_ordered = (tag == "ol")
            self._list_items = []

        elif tag == "li":
            self._li_buf = []

        elif tag == "img":
            src = self._abs(ad.get("src", ""))
            if src and not src.startswith("data:"):
                w, h = ad.get("width"), ad.get("height")
                entry: dict = {"src": src, "alt": ad.get("alt", ""), "caption": ""}
                if w and h and str(w).isdigit() and str(h).isdigit():
                    entry["dimensions"] = [int(w), int(h)]
                self.images.append(entry)
                self.blocks.append({"type": "image", "src": src, "alt": ad.get("alt", ""), "caption": ""})

        elif tag == "a":
            href = self._abs(ad.get("href", ""))
            self._a_href = href if href and not href.startswith("javascript:") else None
            self._a_buf = []

    def handle_endtag(self, tag: str):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = self._flush(self._h_buf)
            if text:
                self._anchor_n += 1
                self.blocks.append({
                    "type": "heading",
                    "level": int(tag[1]),
                    "text": text,
                    "id": f"anchor-{self._anchor_n}",
                })
            self._h_level = 0
            self._h_buf = []

        elif tag == "p":
            text = self._flush(self._p_buf)
            wc = len(text.split())
            if wc > 3:
                self.blocks.append({"type": "paragraph", "text": text, "word_count": wc})
            self._p_buf = []

        elif tag == "th":
            self._t_headers.append(self._flush(self._t_cell_buf))
            self._t_in_th = False
            self._t_cell_buf = []

        elif tag == "td":
            self._t_cur_row.append(self._flush(self._t_cell_buf))
            self._t_in_td = False
            self._t_cell_buf = []

        elif tag == "tr" and self._t_cur_row:
            self._t_rows.append(self._t_cur_row)
            self._t_cur_row = []

        elif tag == "table" and self._in_table:
            headers = self._t_headers or (
                [f"col{i}" for i in range(len(self._t_rows[0]))] if self._t_rows else []
            )
            if headers or self._t_rows:
                sep = ["---"] * len(headers)
                md = (
                    "| " + " | ".join(headers) + " |\n"
                    + "| " + " | ".join(sep) + " |\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in self._t_rows)
                )
                self.blocks.append({
                    "type": "table",
                    "headers": headers,
                    "rows": self._t_rows,
                    "as_markdown": md,
                })
            self._in_table = False

        elif tag == "li":
            text = self._flush(self._li_buf)
            if text:
                self._list_items.append(text)
            self._li_buf = []

        elif tag in ("ul", "ol") and self._list_items:
            self.blocks.append({
                "type": "list",
                "ordered": self._list_ordered,
                "items": self._list_items,
            })
            self._list_items = []
            self._in_list = False

        elif tag == "a" and self._a_href:
            text = self._flush(self._a_buf)
            self.links.append({
                "text": text,
                "href": self._a_href,
                "is_external": self._is_external(self._a_href),
            })
            self._a_href = None
            self._a_buf = []

    def handle_data(self, data: str):
        if not self.stack:
            return
        top = self.stack[-1]
        if top in self.SKIP_TAGS:
            return
        if self._h_level:
            self._h_buf.append(data)
        if top == "p":
            self._p_buf.append(data)
        if self._t_in_th or self._t_in_td:
            self._t_cell_buf.append(data)
        if self._in_list and top == "li":
            self._li_buf.append(data)
        if self._a_href is not None:
            self._a_buf.append(data)


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _extract_meta(html: str, name: str) -> str:
    m = re.search(
        rf'<meta[^>]+(?:name|property)=["\'](?:og:)?{name}["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.I
    ) or re.search(
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:og:)?{name}["\']',
        html, re.I
    )
    return m.group(1).strip() if m else ""


def _build_semantic_schema(html: str, url: str, status_code: int) -> dict:
    """Build the full hybrid semantic schema from raw HTML."""
    parser = _SemanticParser(url)
    try:
        parser.feed(html)
    except Exception:
        pass

    plain = re.sub(r"<[^>]+>", " ", html)
    plain = re.sub(r"\s+", " ", plain).strip()

    # ── entity extraction ─────────────────────────────────────────────────────
    emails = list(set(re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", plain)))
    phones = list(set(re.findall(r"(?:\+?\d[\d\s\-().]{7,}\d)", plain)))[:20]
    dates  = list(set(re.findall(
        r"\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}[/\-]\d{2}[/\-]\d{2}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
        plain,
    )))[:20]

    # ── key phrases (word frequency) ─────────────────────────────────────────
    STOP = {
        "this","that","with","from","have","will","your","they","been","more",
        "also","into","than","then","when","what","which","their","about",
        "would","there","these","after","other","some","such","just","very",
        "were","being","those","where","while","should","could","might","does",
        "here","only","over","back","even","well","each","many","most","want",
    }
    words = re.findall(r"\b[a-zA-Z]{4,}\b", plain.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in STOP:
            freq[w] = freq.get(w, 0) + 1
    key_phrases = [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:15]]

    # ── reading time & summary ────────────────────────────────────────────────
    total_words = len(plain.split())
    reading_time = max(1, round(total_words / 200))

    paras = [b["text"] for b in parser.blocks if b["type"] == "paragraph"][:3]
    raw_sum = " ".join(paras)
    summary = (raw_sum[:500] + "...") if len(raw_sum) > 500 else raw_sum

    # ── content type heuristic ────────────────────────────────────────────────
    lp = plain.lower()
    if re.search(r"add to cart|buy now|\$\s*\d+|price|in stock|out of stock", lp):
        content_type = "product"
    elif re.search(r"\b(?:function|class|param(?:eter)?s?|returns?|import|install|usage|example|api\b)", lp):
        content_type = "documentation"
    elif (
        any(b["type"] == "table" for b in parser.blocks)
        and sum(1 for b in parser.blocks if b["type"] == "heading") > 3
    ):
        content_type = "listing"
    else:
        content_type = "article"

    # ── meta tags ─────────────────────────────────────────────────────────────
    og_desc = _extract_meta(html, "description")

    return {
        "meta": {
            "url": url,
            "title": _extract_title(html),
            "description": og_desc,
            "scraped_at": datetime.datetime.now(timezone.utc).isoformat() + "Z",
            "content_type": content_type,
            "status_code": status_code,
            "total_words": total_words,
        },
        "content": {
            "blocks": parser.blocks,
        },
        "extracted": {
            "links": parser.links,
            "images": parser.images,
            "entities": {
                "emails": emails,
                "phones": phones,
                "dates": dates,
            },
        },
        "semantic": {
            "summary": summary,
            "key_phrases": key_phrases,
            "reading_time_minutes": reading_time,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-URL CRAWLER
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_one(client: httpx.AsyncClient, url: str) -> dict:
    """Fetch and parse one URL. Returns the semantic schema or an error dict."""
    try:
        resp = await client.get(url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        return _build_semantic_schema(resp.text, str(resp.url), resp.status_code)
    except httpx.HTTPStatusError as e:
        return {
            "meta": {"url": url, "status_code": e.response.status_code, "scraped_at": datetime.datetime.now(timezone.utc).isoformat() + "Z"},
            "error": f"HTTP {e.response.status_code}",
        }
    except Exception as e:
        return {
            "meta": {"url": url, "scraped_at": datetime.datetime.now(timezone.utc).isoformat() + "Z"},
            "error": str(e),
        }


def _discover_links(schema: dict, same_domain_only: bool, base_url: str) -> list[str]:
    """Return crawlable child URLs from a parsed page."""
    links = schema.get("extracted", {}).get("links", [])
    urls = []
    base_netloc = urlparse(base_url).netloc
    for lnk in links:
        href = lnk.get("href", "")
        if not href or not href.startswith("http"):
            continue
        if same_domain_only and urlparse(href).netloc != base_netloc:
            continue
        if href not in urls:
            urls.append(href)
    return urls


# ══════════════════════════════════════════════════════════════════════════════
# NODE
# ══════════════════════════════════════════════════════════════════════════════

@register_action(
    type="web_scrape",
    label="Web Scrape",
    icon="🕷️",
    category="Actions",
    description="Scrape one or more URLs and return structured semantic JSON with blocks, links, images, entities, and a summary.",
    config_schema={
        "url": {
            "type": "string",
            "label": "URL",
            "required": True,
            "template": True,
            "description": "The URL to scrape. Supports {{ node_id.field }} interpolation.",
        },
        "extra_urls": {
            "type": "string",
            "label": "Additional URLs (one per line)",
            "required": False,
            "multiline": True,
            "description": "Optionally scrape multiple URLs in one node. One URL per line.",
        },
        "crawl_depth": {
            "type": "number",
            "label": "Crawl Depth",
            "required": False,
            "min": 0,
            "max": 3,
            "description": "How many link-hops to follow from the starting URL. 0 = only the given URL(s). Max 3.",
        },
        "same_domain_only": {
            "type": "boolean",
            "label": "Same Domain Only",
            "required": False,
            "description": "When crawling, only follow links on the same domain as the starting URL.",
        },
        "max_pages": {
            "type": "number",
            "label": "Max Pages",
            "required": False,
            "min": 1,
            "max": 50,
            "description": "Maximum total pages to scrape (including crawled pages). Default: 10.",
        },
        "concurrency": {
            "type": "number",
            "label": "Concurrency",
            "required": False,
            "min": 1,
            "max": 10,
            "description": "How many pages to fetch in parallel. Default: 3.",
        },
        "output_format": {
            "type": "select",
            "label": "Output Format",
            "required": False,
            "options": ["full", "summary_only", "blocks_only"],
            "description": "full = entire schema. summary_only = meta + semantic only. blocks_only = meta + content blocks only. Default: full.",
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename output keys. JSON: {"new_key": "old_key"}',
        },
    },
    is_llm_type=False,
    is_loop_type=False,
)
async def handle_web_scrape(
    config: dict,
    input_data: dict,
    context: dict,
) -> dict:

    # ── validate & resolve ────────────────────────────────────────────────────
    url = str(config.get("url", "")).strip()
    if not url:
        return {"error": "missing field: url", "__input_error__": True}
    if not re.match(r"https?://", url):
        return {"error": f"invalid field: url must start with http:// or https://, got '{url}'", "__input_error__": True}

    # SSRF guard — block private/loopback ranges
    _ssrf_blocked = re.compile(
        r"https?://(?:localhost|127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.0\.0\.0|::1)",
        re.I,
    )
    if _ssrf_blocked.match(url):
        raise ValueError(f"SSRF blocked: private/loopback URL not allowed: {url}")

    # extra URLs
    extra_raw = (config.get("extra_urls") or "").strip()
    extra_urls: list[str] = []
    for line in extra_raw.splitlines():
        line = line.strip()
        if line and re.match(r"https?://", line):
            if not _ssrf_blocked.match(line):
                extra_urls.append(line)

    start_urls = [url] + extra_urls

    try:
        crawl_depth = max(0, min(int(config.get("crawl_depth") or 0), 3))
    except (TypeError, ValueError):
        crawl_depth = 0

    same_domain = bool(config.get("same_domain_only", True))

    try:
        max_pages = max(1, min(int(config.get("max_pages") or 10), 50))
    except (TypeError, ValueError):
        max_pages = 10

    try:
        concurrency = max(1, min(int(config.get("concurrency") or 3), 10))
    except (TypeError, ValueError):
        concurrency = 3

    output_format = (config.get("output_format") or "full").strip().lower()
    if output_format not in ("full", "summary_only", "blocks_only"):
        output_format = "full"

    # ── crawl & scrape ────────────────────────────────────────────────────────
    ua = "Mozilla/5.0 (compatible; M8M-Bot/1.0; +https://m8m.ai)"
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(u, 0) for u in start_urls]
    all_pages: list[dict] = []
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(headers={"User-Agent": ua}) as client:
        while queue and len(all_pages) < max_pages:
            # take a batch up to concurrency
            batch = []
            while queue and len(batch) < concurrency and len(all_pages) + len(batch) < max_pages:
                item = queue.pop(0)
                u, depth = item
                if u in visited:
                    continue
                visited.add(u)
                batch.append((u, depth))

            if not batch:
                break

            async def _fetch_sem(u: str, d: int) -> tuple[dict, int]:
                async with sem:
                    return await _fetch_one(client, u), d

            results = await asyncio.gather(*[_fetch_sem(u, d) for u, d in batch])

            for schema, depth in results:
                all_pages.append(schema)
                # discover child links if crawl_depth allows
                if depth < crawl_depth and "error" not in schema:
                    children = _discover_links(schema, same_domain, url)
                    for child in children:
                        if child not in visited and len(queue) + len(all_pages) < max_pages * 3:
                            queue.append((child, depth + 1))

    # ── apply output_format filter ────────────────────────────────────────────
    def _trim(page: dict) -> dict:
        if output_format == "summary_only":
            return {
                "meta": page.get("meta", {}),
                "semantic": page.get("semantic", {}),
                "error": page.get("error"),
            }
        if output_format == "blocks_only":
            return {
                "meta": page.get("meta", {}),
                "content": page.get("content", {}),
                "error": page.get("error"),
            }
        return page  # full

    trimmed = [_trim(p) for p in all_pages]
    # remove None error keys
    for p in trimmed:
        if p.get("error") is None:
            p.pop("error", None)

    # ── single vs multi output ────────────────────────────────────────────────
    if len(trimmed) == 1:
        page = trimmed[0]
        if "error" in page:
            return {"error": page["error"], "__service_error__": True}
        return {
            "page_count": 1,
            "pages": trimmed,
            # convenience: top-level fields for single-URL use
            "meta": page.get("meta", {}),
            "content": page.get("content", {}),
            "extracted": page.get("extracted", {}),
            "semantic": page.get("semantic", {}),
        }

    return {
        "page_count": len(trimmed),
        "pages": trimmed,
        "scraped_at": datetime.datetime.now(timezone.utc).isoformat() + "Z",
    }


# ── Standalone test ────────────────────────────────────────────────────────────
# import asyncio
# from nodes.web_scrape import handle_web_scrape
#
# # Single URL
# result = asyncio.run(handle_web_scrape(
#     config={
#         "url": "https://example.com",
#         "crawl_depth": 0,
#         "output_format": "full",
#     },
#     input_data={},
#     context={},
# ))
# print(result["meta"]["title"])
# print(result["semantic"]["summary"])
# print(len(result["content"]["blocks"]), "blocks")
#
# # Multi-URL with crawl
# result2 = asyncio.run(handle_web_scrape(
#     config={
#         "url": "https://docs.python.org/3/",
#         "crawl_depth": 1,
#         "same_domain_only": True,
#         "max_pages": 5,
#         "concurrency": 3,
#         "output_format": "summary_only",
#     },
#     input_data={},
#     context={},
# ))
# print(result2["page_count"], "pages scraped")
# ──────────────────────────────────────────────────────────────────────────────
