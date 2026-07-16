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
# SCRAPING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class _SemanticParser(HTMLParser):
    """Stateful HTML parser that produces semantic content blocks."""

    SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "meta", "link"}

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.stack: list[str] = []

        self.blocks: list[dict] = []
        self.links: list[dict] = []
        self.images: list[dict] = []

        # heading state
        self._h_level = 0
        self._h_buf: list[str] = []
        self._anchor_n = 0

        # paragraph state
        self._p_buf: list[str] = []

        # table state
        self._in_table = False
        self._t_headers: list[str] = []
        self._t_rows: list[list[str]] = []
        self._t_cur_row: list[str] = []
        self._t_cell_buf: list[str] = []
        self._t_in_th = False
        self._t_in_td = False

        # list state
        self._in_list = False
        self._list_ordered = False
        self._list_items: list[str] = []
        self._li_buf: list[str] = []

        # anchor state
        self._a_href: str | None = None
        self._a_buf: list[str] = []

    # ── utilities ─────────────────────────────────────────────────────────────

    def _abs(self, href: str) -> str:
        return urljoin(self.base_url, href) if href else ""

    def _is_external(self, url: str) -> bool:
        try:
            return urlparse(url).netloc != urlparse(self.base_url).netloc
        except Exception:
            return False

    def _flush_text(self, buf: list[str]) -> str:
        return re.sub(r"\s+", " ", " ".join(buf)).strip()

    # ── HTMLParser callbacks ──────────────────────────────────────────────────

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
                w = ad.get("width")
                h = ad.get("height")
                dims = [int(w), int(h)] if w and h and str(w).isdigit() and str(h).isdigit() else None
                entry = {"src": src, "alt": ad.get("alt", ""), "caption": ""}
                if dims:
                    entry["dimensions"] = dims
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
            text = self._flush_text(self._h_buf)
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
            text = self._flush_text(self._p_buf)
            wc = len(text.split())
            if wc > 3:
                self.blocks.append({"type": "paragraph", "text": text, "word_count": wc})
            self._p_buf = []

        elif tag == "th":
            self._t_headers.append(self._flush_text(self._t_cell_buf))
            self._t_in_th = False
            self._t_cell_buf = []

        elif tag == "td":
            self._t_cur_row.append(self._flush_text(self._t_cell_buf))
            self._t_in_td = False
            self._t_cell_buf = []

        elif tag == "tr" and self._t_cur_row:
            self._t_rows.append(self._t_cur_row)
            self._t_cur_row = []

        elif tag == "table" and self._in_table:
            headers = self._t_headers or ([f"col{i}" for i in range(len(self._t_rows[0]))] if self._t_rows else [])
            if headers or self._t_rows:
                sep = ["---"] * len(headers)
                md = (
                    "| " + " | ".join(headers) + " |\n"
                    "| " + " | ".join(sep) + " |\n"
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
            text = self._flush_text(self._li_buf)
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
            text = self._flush_text(self._a_buf)
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


def _parse_page(html: str, url: str) -> dict:
    """Full semantic parse of an HTML page."""
    parser = _SemanticParser(url)
    try:
        parser.feed(html)
    except Exception:
        pass

    plain = re.sub(r"<[^>]+>", " ", html)
    plain = re.sub(r"\s+", " ", plain)

    # entity extraction
    emails = list(set(re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", plain)))
    phones = list(set(re.findall(r"(?:\+?\d[\d\s\-().]{7,}\d)", plain)))[:20]
    dates  = list(set(re.findall(
        r"\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}[/\-]\d{2}[/\-]\d{2}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
        plain,
    )))[:20]

    # key phrases via word frequency
    words = re.findall(r"\b[a-zA-Z]{4,}\b", plain.lower())
    stop = {
        "this","that","with","from","have","will","your","they","been","more",
        "also","into","than","then","when","what","which","their","about",
        "would","there","these","after","other","some","such","just","very",
        "were","been","being","those","where","while","should","could","might",
    }
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    key_phrases = [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:15]]

    total_words = len(plain.split())
    reading_time = max(1, round(total_words / 200))

    # summary from first paragraphs
    paras = [b["text"] for b in parser.blocks if b["type"] == "paragraph"][:3]
    raw_summary = " ".join(paras)
    summary = (raw_summary[:500] + "...") if len(raw_summary) > 500 else raw_summary

    # content type heuristic
    lp = plain.lower()
    if re.search(r"add to cart|buy now|\$\s*\d+|price|in stock", lp):
        content_type = "product"
    elif re.search(r"\b(?:function|class|param|returns?|import|install|usage|example|api)\b", lp):
        content_type = "documentation"
    elif any(b["type"] == "table" for b in parser.blocks) and sum(1 for b in parser.blocks if b["type"] == "heading") > 3:
        content_type = "listing"
    else:
        content_type = "article"

    return {
        "blocks": parser.blocks,
        "links": parser.links,
        "images": parser.images,
        "entities": {"emails": emails, "phones": phones, "dates": dates},
        "semantic": {"summary": summary, "key_phrases": key_phrases, "reading_time_minutes": reading_time},
        "content_type": content_type,
        "total_words": total_words,
    }


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


async def _scrape_url(client: httpx.AsyncClient, url: str, snippet: str = "") -> dict:
    try:
        resp = await client.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        parsed = _parse_page(resp.text, str(resp.url))
        return {
            "meta": {
                "url": str(resp.url),
                "title": _extract_title(resp.text),
                "scraped_at": datetime.datetime.now(timezone.utc).isoformat() + "Z",
                "content_type": parsed["content_type"],
                "status_code": resp.status_code,
                "total_words": parsed["total_words"],
                "snippet": snippet,
            },
            "content": {"blocks": parsed["blocks"]},
            "extracted": {
                "links": parsed["links"],
                "images": parsed["images"],
                "entities": parsed["entities"],
            },
            "semantic": parsed["semantic"],
        }
    except httpx.HTTPStatusError as e:
        return {"url": url, "error": f"HTTP {e.response.status_code}", "snippet": snippet}
    except Exception as e:
        return {"url": url, "error": str(e), "snippet": snippet}


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH PROVIDERS
# ══════════════════════════════════════════════════════════════════════════════

async def _search_brave(q: str, key: str, n: int, client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        params={"q": q, "count": min(n, 20)},
        timeout=15,
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")}
        for x in r.json().get("web", {}).get("results", [])
    ]


async def _search_serpapi(q: str, key: str, n: int, client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(
        "https://serpapi.com/search",
        params={"q": q, "api_key": key, "num": min(n, 10), "engine": "google"},
        timeout=15,
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
        for x in r.json().get("organic_results", [])
    ]


async def _search_google_cse(q: str, key: str, cx: str, n: int, client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"q": q, "key": key, "cx": cx, "num": min(n, 10)},
        timeout=15,
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
        for x in r.json().get("items", [])
    ]


# ══════════════════════════════════════════════════════════════════════════════
# NODE
# ══════════════════════════════════════════════════════════════════════════════

@register_action(
    type="web_search_scrape",
    label="Web Search & Scrape",
    icon="🔍",
    category="Actions",
    description="Search the web via an API then scrape each result page into structured semantic JSON.",
    config_schema={
        "query": {
            "type": "string",
            "label": "Search Query",
            "required": True,
            "template": True,
            "description": "What to search for. Supports {{ node_id.field }} interpolation.",
        },
        "search_provider": {
            "type": "select",
            "label": "Search Provider",
            "required": True,
            "options": ["brave", "serpapi", "google_cse"],
            "description": "Which search API to use.",
        },
        "api_key": {
            "type": "string",
            "label": "API Key",
            "required": True,
            "secret": True,
            "description": "API key for the chosen search provider.",
        },
        "google_cx": {
            "type": "string",
            "label": "Google CSE ID (cx)",
            "required": False,
            "description": "Required only when provider is google_cse.",
            "visibleWhen": {"search_provider": "google_cse"},
        },
        "result_count": {
            "type": "number",
            "label": "Number of Results",
            "required": False,
            "min": 1,
            "max": 20,
            "description": "How many search results to return. Default: 5.",
        },
        "scrape_results": {
            "type": "boolean",
            "label": "Scrape Result Pages",
            "required": False,
            "description": "Fetch and parse each result URL into semantic blocks.",
        },
        "scrape_concurrency": {
            "type": "number",
            "label": "Scrape Concurrency",
            "required": False,
            "min": 1,
            "max": 10,
            "description": "How many pages to scrape in parallel. Default: 3.",
            "visibleWhen": {"scrape_results": True},
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
async def handle_web_search_scrape(
    config: dict,
    input_data: dict,
    context: dict,
) -> dict:

    # ── validate ──────────────────────────────────────────────────────────────
    query = str(config.get("query", "")).strip()
    if not query:
        return {"error": "missing field: query", "__input_error__": True}

    provider = (config.get("search_provider") or "").strip().lower()
    if provider not in ("brave", "serpapi", "google_cse"):
        return {"error": "invalid field: search_provider must be brave, serpapi, or google_cse", "__input_error__": True}

    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        return {"error": "missing field: api_key", "__input_error__": True}

    google_cx = (config.get("google_cx") or "").strip()
    if provider == "google_cse" and not google_cx:
        return {"error": "missing field: google_cx is required when provider is google_cse", "__input_error__": True}

    try:
        result_count = max(1, min(int(config.get("result_count") or 5), 20))
    except (TypeError, ValueError):
        result_count = 5

    scrape = bool(config.get("scrape_results", True))

    try:
        concurrency = max(1, min(int(config.get("scrape_concurrency") or 3), 10))
    except (TypeError, ValueError):
        concurrency = 3

    # ── execute ───────────────────────────────────────────────────────────────
    ua = "Mozilla/5.0 (compatible; UPIAA-Bot/1.0; +https://UPIAA.ai)"
    async with httpx.AsyncClient(headers={"User-Agent": ua}) as client:

        # search
        try:
            if provider == "brave":
                results = await _search_brave(query, api_key, result_count, client)
            elif provider == "serpapi":
                results = await _search_serpapi(query, api_key, result_count, client)
            else:
                results = await _search_google_cse(query, api_key, google_cx, result_count, client)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise ValueError(f"Search API auth failed ({e.response.status_code}): check your api_key.")
            return {"error": f"Search API error: HTTP {e.response.status_code}", "__service_error__": True}
        except Exception as e:
            return {"error": f"Search request failed: {str(e)}", "__service_error__": True}

        # scrape
        scraped: list[dict] = []
        if scrape and results:
            sem = asyncio.Semaphore(concurrency)

            async def _scrape_sem(r: dict) -> dict:
                async with sem:
                    return await _scrape_url(client, r["url"], r.get("snippet", ""))

            scraped = list(await asyncio.gather(*[_scrape_sem(r) for r in results]))

    return {
        "query": query,
        "provider": provider,
        "result_count": len(results),
        "search_results": results,
        "scraped_pages": scraped,
        "searched_at": datetime.datetime.now(timezone.utc).isoformat() + "Z",
    }


# ── Standalone test ────────────────────────────────────────────────────────────
# import asyncio
# from nodes.web_search_scrape import handle_web_search_scrape
#
# result = asyncio.run(handle_web_search_scrape(
#     config={
#         "query": "Python asyncio tutorial",
#         "search_provider": "brave",
#         "api_key": "YOUR_BRAVE_KEY",
#         "result_count": 3,
#         "scrape_results": True,
#         "scrape_concurrency": 3,
#     },
#     input_data={},
#     context={},
# ))
# print(result["query"], "->", result["result_count"], "results")
# for page in result["scraped_pages"]:
#     m = page.get("meta", {})
#     print(" -", m.get("title", "?"), "|", len(page.get("content", {}).get("blocks", [])), "blocks")
# ──────────────────────────────────────────────────────────────────────────────
