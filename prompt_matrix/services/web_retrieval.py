"""The allowlist-bounded fetch that grounds an unanchored claim (2C).

The drawer's gap analysis ends with a search query. This module is what happens
to that query: a Brave search filtered to ``config/authoritative_domains.yaml``,
then — only when the user asks for one card to be fetched — one HTTPS page, read
under hard caps, scanned for instruction-like content with the same scan an
upload gets, stored as an ordinary source row, and followed by a re-run of the
anchoring gate so the paragraph is grounded or it is not.

What this path does *not* do is the point of it. It never rewrites the claim: the
paragraph either anchors to the fetched page's own sentence or it stays
unanchored. Search results that fail the allowlist are reported as rejected, and
a fetch from a denied host is refused before any request leaves the box, because
the matcher runs first.

Limits (2C.4): HTTPS only, 5 MB, 30 s. Every redirect hop is re-matched, so a
permitted domain cannot hand the fetch to a denied one.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

try:
    from ..db.jdf_repository import fetch_latest_jdf_or_empty, save_jdf_revision
    from ..db.substrate_repository import list_substrate_for_project, upsert_substrate_entry
    from ..lib.source_labels import fetched_label
    from ..models.jdf import (
        _MIN_CLAIM_TOKENS,
        _tokenize,
        attach_substrate_provenance_to_tree,
    )
    from .audit_summary import _entailment_verdict, _provenance_counts
    from .authoritative_domains import ALLOW, decision_for, host_of
    from .compile_guard import scan_source_instruction_like, wrap_untrusted_source
    from .entailment import _claim_sources, check_entailment
    from .evidence_gap import strip_urls
except ImportError:  # pragma: no cover
    from db.jdf_repository import fetch_latest_jdf_or_empty, save_jdf_revision
    from db.substrate_repository import list_substrate_for_project, upsert_substrate_entry
    from lib.source_labels import fetched_label
    from models.jdf import (  # type: ignore[no-redef]
        _MIN_CLAIM_TOKENS,
        _tokenize,
        attach_substrate_provenance_to_tree,
    )
    from services.audit_summary import (  # type: ignore[no-redef]
        _entailment_verdict,
        _provenance_counts,
    )
    from services.authoritative_domains import (  # type: ignore[no-redef]
        ALLOW,
        decision_for,
        host_of,
    )
    from services.compile_guard import (  # type: ignore[no-redef]
        scan_source_instruction_like,
        wrap_untrusted_source,
    )
    from services.entailment import (  # type: ignore[no-redef]
        _claim_sources,
        check_entailment,
    )
    from services.evidence_gap import strip_urls  # type: ignore[no-redef]

_log = logging.getLogger(__name__)

MAX_FETCH_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 30.0
MAX_REDIRECTS = 3
SNIPPET_CHARS = 120
SEARCH_CANDIDATES = 10
RESULT_CARDS = 3
MIN_PAGE_CHARS = 200
QUERY_CHARS = 200
USER_AGENT = "AssureBot/1.0 (+https://getassureai.com; source verification)"

# A page of these types is text the matcher can quote. A PDF or an image is not a
# fetch this path can read, so it is refused rather than stored as gibberish.
TEXT_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain", "text/markdown"}


class RetrievalError(Exception):
    """A refusal, carrying the reason the caller reports and the log records."""

    def __init__(self, reason: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status


@dataclass(frozen=True)
class FetchedPage:
    """One page, read and cleaned. ``text`` is what is stored and matched."""

    url: str
    host: str
    title: str
    text: str
    content_type: str
    bytes_read: int
    instruction_like: bool
    instruction_hits: tuple[str, ...]
    retrieved_on: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "host": self.host,
            "title": self.title,
            "content_type": self.content_type,
            "bytes": self.bytes_read,
            "text_chars": len(self.text),
            "instruction_like": self.instruction_like,
            "instruction_hits": list(self.instruction_hits),
            "retrieved_on": self.retrieved_on,
        }


# ---------------------------------------------------------------------------
# 2C.3 — the search, filtered to the allowlist
# ---------------------------------------------------------------------------
def sanitize_query(query: str) -> str:
    """The model's query as a query: whitespace-collapsed, URL/domain tokens gone.

    The gap prompt forbids naming a URL, and a search driven by one would defeat
    the allowlist by asking the search engine for a specific host. Cheap to
    enforce here rather than trusting the instruction.
    """
    text = " ".join(strip_urls(query).split())
    return text[:QUERY_CHARS].strip()


def _card(url: str, host: str, category: str, title: str, snippet: str) -> dict[str, Any]:
    return {
        "url": url,
        "host": host,
        "category": category,
        "title": " ".join(str(title or "").split())[:SNIPPET_CHARS],
        "snippet": " ".join(str(snippet or "").split())[:SNIPPET_CHARS],
    }


def search_authoritative(query: str, *, limit: int = RESULT_CARDS) -> dict[str, Any]:
    """Brave results for ``query``, filtered to the allowlist, top ``limit`` cards.

    The filter is the allowlist's own decision function — the same one the fetch
    uses — so a result that would be refused at fetch time is never shown as a
    card either. Nothing here claims the page supports the claim; that is decided
    after a fetch, by the gate.
    """
    clean = sanitize_query(query)
    if not clean:
        raise RetrievalError("empty_query", "No search query for this claim yet.")
    try:
        results = search_brave_web(clean, count=SEARCH_CANDIDATES)
    except Exception as exc:      # transport, auth, provider error
        raise RetrievalError("search_failed", f"The search failed: {type(exc).__name__}", status=502) from exc
    cards: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for result in results:
        url = str(result.get("source_url") or "").strip()
        host = host_of(url)
        verdict, category = decision_for(host)
        if verdict != ALLOW or not url:
            rejected.append({"host": host, "reason": category})
            continue
        cards.append(
            _card(
                url,
                host,
                category,
                result.get("title") or "",
                (result.get("snippet") or "").replace("&#x27;", "'"),
            )
        )
        if len(cards) >= limit:
            break
    return {
        "ok": True,
        "query": clean,
        "hosts_considered": len(results),
        "hosts_rejected": rejected,
        "cards": cards,
    }


# ---------------------------------------------------------------------------
# 2C.4 — the fetch, under the caps, HTTPS only, allowlist re-checked per hop
# ---------------------------------------------------------------------------
def validate_target(url: str) -> tuple[str, str, str]:
    """``(url, host, category)`` for a fetchable URL — raises before any request."""
    raw = str(url or "").strip()
    if not raw:
        raise RetrievalError("empty_url", "No URL to fetch.")
    scheme = urlsplit(raw).scheme.lower()
    if scheme != "https":
        raise RetrievalError(
            "https_required",
            f"Only https:// sources can be fetched (got {scheme or 'no scheme'}://).",
        )
    host = host_of(raw)
    verdict, category = decision_for(host)
    if verdict != ALLOW:
        raise RetrievalError(
            "host_not_allowed",
            f"{host or 'that host'} is not on the authoritative-domain allowlist.",
            status=403,
        )
    return raw, host, category


class _MainTextExtractor(HTMLParser):
    """Main text of a page: scripts/styles/chrome dropped, ``main`` preferred.

    Stdlib only — the fetch path adds no parser dependency. ``<main>``/``<article>``
    text wins when the page has enough of it, because that is the part a claim can
    be anchored to; otherwise the whole page's text is the fallback.
    """

    SKIP_TAGS = {
        "script", "style", "noscript", "template", "svg", "iframe",
        "nav", "footer", "header", "aside", "form", "button", "select", "option",
    }
    MAIN_TAGS = {"main", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.main_parts: list[str] = []
        self.all_parts: list[str] = []
        self._skip_depth = 0
        self._main_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag in self.MAIN_TAGS:
            self._main_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.MAIN_TAGS and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        self.all_parts.append(text)
        if self._main_depth:
            self.main_parts.append(text)

    def title(self) -> str:
        return " ".join(unescape(" ".join(self.title_parts)).split())

    def text(self) -> str:
        main = " ".join(self.main_parts)
        body = " ".join(self.all_parts)
        chosen = main if len(main) >= MIN_PAGE_CHARS else body
        return _collapse_lines(chosen)


def _collapse_lines(text: str) -> str:
    lines = [" ".join(line.split()) for line in unescape(str(text or "")).splitlines()]
    return "\n".join(line for line in lines if line)


def _decode(body: bytes, charset: str | None) -> str:
    for candidate in (charset, "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def fetch_authoritative_page(url: str, *, client: Any = None) -> FetchedPage:
    """Fetch one allowlisted HTTPS page under the size and time caps.

    Every hop is matched before it is requested, so a redirect to a denied host is
    refused without a request to it. Raises ``RetrievalError`` with a reason for
    each refusal.
    """
    target, host, _category = validate_target(url)

    import httpx

    owns_client = client is None
    http = client or httpx.Client(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"},
    )
    current = target
    total = 0
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            with http.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location") or ""
                    if not location:
                        raise RetrievalError("redirect_without_location", "The page redirected nowhere.")
                    current = urljoin(current, location)
                    target, host, _category = validate_target(current)
                    continue
                if response.status_code >= 400:
                    raise RetrievalError(
                        "http_error",
                        f"The page answered HTTP {response.status_code}.",
                        status=502,
                    )
                content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                if content_type and content_type not in TEXT_CONTENT_TYPES:
                    raise RetrievalError(
                        "unsupported_content_type",
                        f"{content_type} is not a page this path can read.",
                    )
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_FETCH_BYTES:
                    raise RetrievalError(
                        "too_large",
                        f"The page is larger than {MAX_FETCH_BYTES // (1024 * 1024)} MB.",
                    )
                chunks: list[bytes] = []
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_FETCH_BYTES:
                        raise RetrievalError(
                            "too_large",
                            f"The page is larger than {MAX_FETCH_BYTES // (1024 * 1024)} MB.",
                        )
                    chunks.append(chunk)
                body = _decode(b"".join(chunks), response.charset_encoding)
            break
        else:
            raise RetrievalError("too_many_redirects", "The page redirected too many times.")
    except httpx.TimeoutException as exc:
        raise RetrievalError(
            "timeout", f"The page took longer than {int(FETCH_TIMEOUT_SECONDS)} s.", status=504
        ) from exc
    except httpx.HTTPError as exc:
        raise RetrievalError(
            "fetch_failed", f"Could not fetch the page: {type(exc).__name__}", status=502
        ) from exc
    finally:
        if owns_client:
            http.close()

    extractor = _MainTextExtractor()
    try:
        extractor.feed(body)
        extractor.close()
    except Exception:   # a malformed page still has the text parsed before the break
        _log.warning("[retrieval] partial parse of %s", current)
    text = extractor.text()
    if len(text) < MIN_PAGE_CHARS:
        raise RetrievalError(
            "no_readable_text",
            "The page has no readable text to ground a claim in.",
            status=502,
        )
    hits = scan_source_instruction_like(text)
    return FetchedPage(
        url=current,
        host=host,
        title=extractor.title(),
        text=text,
        content_type="text/html",
        bytes_read=total,
        instruction_like=bool(hits),
        instruction_hits=tuple(hits),
        retrieved_on=datetime.now(UTC).strftime("%Y-%m-%d"),
    )


def tag_for(page: FetchedPage) -> str:
    """The ``fetched_url: <host>`` tag this page's source row carries."""
    return f"fetched_url: {page.host}"


def ingest_fetched_source(project_id: str, page: FetchedPage) -> dict[str, Any]:
    """Store the page as a source row labelled with its host and retrieval date.

    The row is an ordinary vault row — same table, same ingest scan, same
    inclusion flag — so the compile, the SOURCES pane and the counters treat it
    exactly like an upload. Only its label says where it came from.
    """
    label = fetched_label(page.host, page.retrieved_on)
    entry = upsert_substrate_entry(
        project_id,
        filename=label,
        page_count=1,
        extracted_text=page.text,
        tables=[],
        forms=[],
        file_size_bytes=page.bytes_read,
        instruction_like=page.instruction_like,
        instruction_hits=list(page.instruction_hits),
    )
    entry["fetched_url"] = page.host
    entry["label"] = label
    return entry


# ---------------------------------------------------------------------------
# The gate re-run: the fetched page either anchors the claim or it does not
# ---------------------------------------------------------------------------
def _anchor_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Vault rows as the anchoring gate reads them.

    A row the ingest scan flagged is wrapped in the untrusted-data delimiter
    before it is matched, exactly as ``routers/draft.py`` feeds the compile: the
    page is content to quote, never instructions to follow.
    """
    prepared: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("extracted_text") or "")
        if text and scan_source_instruction_like(text):
            text = wrap_untrusted_source(text)
        prepared.append({**row, "extracted_text": text})
    return prepared


def _anchored_by_origin(node: dict[str, Any] | None, fetched_hosts: dict[str, str]) -> str:
    """``"fetched"``/``"uploaded"``/``""`` for a paragraph, from its provenance rows."""
    if not node:
        return ""
    rows = [row for row in (node.get("provenance") or []) if isinstance(row, dict)]
    quoted = [row for row in rows if str(row.get("extracted_quote") or "").strip()]
    if not quoted:
        return ""
    # A paragraph anchored to both is counted as uploaded: the source it rests on
    # is one the user supplied, and the fetched page is a second opinion.
    if all(str(row.get("source_id") or "") in fetched_hosts for row in quoted):
        return "fetched"
    return "uploaded"


def _iter_nodes(tree: dict[str, Any]):
    """Sections and their direct children, as live references.

    ``models.jdf.flatten_nodes`` deep-copies the tree (it goes through
    ``document_to_dict``), so a node taken from it cannot be written back — the
    entailment record would be computed and dropped. This walks the same shape
    the gate and ``services/audit_summary`` walk, without the copy.
    """
    for section in (tree or {}).get("body") or []:
        if not isinstance(section, dict):
            continue
        yield section
        for child in section.get("children") or []:
            if isinstance(child, dict):
                yield child


def origin_counts(document: dict[str, Any], fetched_hosts: dict[str, str]) -> dict[str, int]:
    """Eligible paragraphs split by where the source they cite came from."""
    counts = {"anchored_uploaded": 0, "anchored_fetched": 0, "unanchored": 0}
    for node in _iter_nodes(document):
        if str(node.get("type") or "") != "paragraph":
            continue
        if len(_tokenize(str(node.get("content") or ""))) < _MIN_CLAIM_TOKENS:
            continue
        origin = _anchored_by_origin(node, fetched_hosts)
        if origin == "fetched":
            counts["anchored_fetched"] += 1
        elif origin == "uploaded":
            counts["anchored_uploaded"] += 1
        else:
            counts["unanchored"] += 1
    return counts


def fetched_hosts_for(project_id: str) -> dict[str, str]:
    """``source_id -> host`` for the project's fetched rows."""
    mapping: dict[str, str] = {}
    for row in list_substrate_for_project(project_id):
        host = str(row.get("fetched_url") or "").strip()
        if host:
            mapping[str(row.get("id") or "")] = host
    return mapping


def reanchor(project_id: str, *, node_id: str = "") -> dict[str, Any]:
    """Re-run the anchoring gate over the project's sources and persist the result.

    The gate is called unchanged (``models/jdf.py:attach_substrate_provenance_to_tree``):
    it stamps the source sentence a paragraph matches, appends to whatever
    provenance the paragraph already carries, and leaves the entailment layer
    alone. So a paragraph the fetched page does not support keeps its state, and
    one it does support moves from unanchored to anchored.

    A fetched anchor is then handed to the entailment check the compile path uses
    — one call, for the one paragraph the fetch just grounded, cached by
    ``services/entailment_cache`` like every other verdict. Anchoring is a
    wording match; without the check the paragraph would read "anchored" and
    nothing more, and the drawer would have no verdict to show. The check never
    raises: a failed call persists ``unverified`` with its reason, so the
    paragraph reads "not verified" rather than green.

    Persisted only when the gate or the check changed the tree, so a fetch that
    grounds nothing leaves no empty revision behind.
    """
    document = fetch_latest_jdf_or_empty(project_id)
    rows = [
        row
        for row in list_substrate_for_project(project_id, with_text=True)
        if row.get("included") is not False
    ]
    gated = attach_substrate_provenance_to_tree(document, [], _anchor_rows(rows))
    fetched_hosts = fetched_hosts_for(project_id)
    watched = next(
        (node for node in _iter_nodes(gated) if str(node.get("id") or "") == node_id),
        None,
    )
    verdict: dict[str, Any] = {}
    if watched is not None and _anchored_by_origin(watched, fetched_hosts) and not _entailment_verdict(watched):
        _sources = _claim_sources(watched)
        claim = str(watched.get("content") or "").strip()
        source = _sources[0] if _sources else ""
        if claim and source:
            try:
                verdict = dict(check_entailment(claim, source, project_id=project_id))
            except Exception as exc:      # never a silent pass, never a crash
                verdict = {"verdict": "unverified", "reasoning": f"{type(exc).__name__}: {exc}"}
            meta = dict(watched.get("meta") or {})
            prov = dict(meta.get("provenance") or {})
            prov["entailment"] = verdict
            meta["provenance"] = prov
            watched["meta"] = meta

    updated = gated
    changed = updated != document
    if changed:
        save_jdf_revision(
            project_id,
            updated,
            mutation_type="fetched_source_anchor",
            target_node_id=node_id or None,
            change_summary="re-ran the anchoring gate after a fetched source",
        )
    return {
        "ok": True,
        "document": updated,
        "persisted": changed,
        "anchored": bool(_anchored_by_origin(watched, fetched_hosts)),
        "origin": _anchored_by_origin(watched, fetched_hosts),
        "entailment": verdict,
        "stats": _provenance_counts(updated),
        "origins": origin_counts(updated, fetched_hosts),
        "fetched_hosts": fetched_hosts,
    }


# ---------------------------------------------------------------------------
# The Brave client. It lived in services/ground_node.py, which was the /ground
# path's rewrite engine; that path is gone (it rewrote unanchored claims to look
# grounded, which contradicts the product's disclosure model), so the one part
# of it that fetches rather than rewrites moved here. Same request, same
# headers, same shape of result.
# ---------------------------------------------------------------------------
def search_brave_web(query: str, *, count: int = 3) -> list[dict[str, str]]:
    """Brave web results for ``query`` — [] when no key is configured."""
    token = (os.environ.get("BRAVE_API_KEY") or "").strip()
    q = (query or "").strip()[:QUERY_CHARS]
    if not token or not q:
        return []
    import httpx

    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": q, "count": count},
        headers={"Accept": "application/json", "X-Subscription-Token": token},
        timeout=20.0,
    )
    response.raise_for_status()
    web = (response.json() or {}).get("web") or {}
    rows: list[dict[str, str]] = []
    for item in (web.get("results") or [])[:count]:
        rows.append(
            {
                "source_url": str(item.get("url") or ""),
                "snippet": str(item.get("description") or item.get("title") or ""),
                "title": str(item.get("title") or ""),
            }
        )
    return rows
