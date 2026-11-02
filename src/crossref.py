"""
Crossref API client for fetching publication metadata and citation counts.

Uses the Crossref REST API: https://api.crossref.org/documentation

Crossref metadata (authors, title, journal) is immutable per DOI; only citation
counts may change. HTTP responses are cached for 1 month.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import requests_cache

from .doi_utils import normalize_doi
from .logger import print_warn
from .path_safety import safe_path_under

logger = logging.getLogger(__name__)

# Cache Crossref HTTP responses for 1 month
_CACHE_ROOT = os.path.abspath(os.environ.get("CACHE_DIR", "cache"))
_CROSSREF_CACHE_DB = os.path.join(_CACHE_ROOT, "http_cache_crossref")
os.makedirs(_CACHE_ROOT, exist_ok=True)
_crossref_session = requests_cache.CachedSession(
    _CROSSREF_CACHE_DB,
    backend="sqlite",
    expire_after=timedelta(days=30),
    allowable_methods=("GET",),
    allowable_codes=(200, 203, 300, 301),
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CROSSREF_BASE = "https://api.crossref.org"
CROSSREF_WORKS = f"{CROSSREF_BASE}/works"

_logged_failures: set[str] = set()
_crossref_title_cache: dict[str, str | None] | None = None


def _title_cache_file() -> Path | None:
    """Fixed title→DOI cache path under ``CACHE_DIR``, or ``None`` if unsafe."""
    return safe_path_under(_CACHE_ROOT, "crossref_title", "doi_by_title.json")


def _cache_key(title: str, author: str) -> str:
    key = f"{title.strip().lower()}|{(author or '').strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _load_title_cache() -> dict[str, str | None]:
    global _crossref_title_cache
    if _crossref_title_cache is not None:
        return _crossref_title_cache
    cache_file = _title_cache_file()
    if cache_file is None or not cache_file.is_file():
        _crossref_title_cache = {}
        return _crossref_title_cache
    # Re-check containment at the read sink for Snyk/CodeQL path modeling.
    base = os.path.realpath(_CACHE_ROOT)
    candidate = os.path.realpath(cache_file)
    base_prefix = base if base.endswith(os.sep) else base + os.sep
    if not candidate.startswith(base_prefix):
        _crossref_title_cache = {}
        return _crossref_title_cache
    try:
        with open(candidate, encoding="utf-8") as f:
            _crossref_title_cache = json.load(f)
    except (json.JSONDecodeError, OSError):
        _crossref_title_cache = {}
    return _crossref_title_cache


def _save_title_cache() -> None:
    if _crossref_title_cache is None:
        return
    cache_file = _title_cache_file()
    if cache_file is None:
        return
    base = os.path.realpath(_CACHE_ROOT)
    candidate = os.path.realpath(cache_file)
    base_prefix = base if base.endswith(os.sep) else base + os.sep
    if not candidate.startswith(base_prefix):
        return
    try:
        os.makedirs(os.path.dirname(candidate), exist_ok=True)
        with open(candidate, "w", encoding="utf-8") as f:
            json.dump(_crossref_title_cache, f, indent=2)
    except OSError:
        pass


@dataclass
class CrossrefResponse:
    """Publication metadata from Crossref API."""

    title: str | None = None
    journal: str | None = None
    authors: list[str] | None = None
    author_families: list[str] | None = None  # Family names only; used for allowlist check
    year: int | None = None
    url: str | None = None
    citation_count: int | None = None


def _coerce_int(value: Any) -> int | None:
    """Coerce value to int if possible."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def crossref_response_from_message(message: dict[str, Any]) -> CrossrefResponse | None:
    """
    Build a CrossrefResponse from a Crossref API 'message' dict.
    Used when reading from the shared file cache so we don't duplicate API parsing.
    """
    if not message or not isinstance(message, dict):
        return None
    try:
        crossref_authors: list[str] | None = None
        crossref_families: list[str] | None = None
        author_list = message.get("author")
        if isinstance(author_list, list):
            authors = []
            families = []
            for author in author_list:
                if isinstance(author, dict):
                    given = author.get("given", "")
                    family = author.get("family", "")
                    if family:
                        families.append(str(family).strip())
                    parts = [str(x).strip() for x in (given, family) if x]
                    name = " ".join(parts).strip()
                    if name:
                        authors.append(name)
            crossref_authors = authors if authors else None
            crossref_families = families if families else None

        year: int | None = None
        for date_key in ("issued", "published", "published-print", "published-online"):
            date_parts = message.get(date_key, {}).get("date-parts", [[]])
            if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list):
                parts = date_parts[0]
                if parts:
                    year = _coerce_int(parts[0])
                    break

        title = None
        title_list = message.get("title")
        if isinstance(title_list, list) and title_list:
            title = str(title_list[0]).strip() or None

        journal = None
        container = message.get("container-title")
        if isinstance(container, list) and container:
            journal = str(container[0]).strip() or None

        url_val = message.get("URL")
        citation_count = _coerce_int(message.get("is-referenced-by-count"))

        return CrossrefResponse(
            title=title,
            journal=journal,
            authors=crossref_authors,
            author_families=crossref_families,
            year=year,
            url=str(url_val) if url_val else None,
            citation_count=citation_count,
        )
    except (KeyError, IndexError, TypeError):
        return None


def search_doi_by_title(pub_title: str, author_last_name: str) -> str | None:
    """
    Search Crossref by publication title and author; return the best-matching DOI.

    HTTP requests are cached for 1 month. Uses the Crossref REST API
    /works endpoint with query.title and query.author.
    """
    if not pub_title or not pub_title.strip():
        return None

    cache = _load_title_cache()
    key = _cache_key(pub_title.strip(), author_last_name or "")
    if key in cache:
        return cache[key]

    try:
        params = {
            "query.title": pub_title.strip(),
            "rows": 5,
        }
        if author_last_name and author_last_name.strip():
            params["query.author"] = author_last_name.strip()

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        resp = _crossref_session.get(CROSSREF_WORKS, params=params, headers=headers, timeout=30)
        if not resp.ok:
            cache[key] = None
            _save_title_cache()
            return None

        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if not items:
            cache[key] = None
            _save_title_cache()
            return None

        author_lower = author_last_name.strip().lower() if author_last_name else ""
        for item in items:
            doi = item.get("DOI")
            if not doi:
                continue
            if author_lower:
                authors = item.get("author", [])
                for a in authors:
                    if isinstance(a, dict) and author_lower in (a.get("family") or "").lower():
                        cache[key] = doi
                        _save_title_cache()
                        return doi
            else:
                cache[key] = doi
                _save_title_cache()
                return doi
        # No author filter, or no author match: return first result if no author given
        result = items[0].get("DOI") if not author_lower else None
        cache[key] = result
        _save_title_cache()
        return result
    except (requests.RequestException, KeyError, TypeError, IndexError) as e:
        if "crossref_title_search" not in _logged_failures:
            print_warn(f"Crossref title search failed for '{pub_title[:50]}...': {e}")
            _logged_failures.add("crossref_title_search")
        cache[key] = None
        _save_title_cache()
        return None


def fetch_crossref_details(doi: str, force_refresh: bool = False) -> CrossrefResponse | None:
    """
    Fetch publication metadata and citation count from Crossref API.
    Uses HTTP cache (30 days). For code that shares the 1-week file cache with the
    /crossref endpoint, use get_crossref_metadata_cached() in scholar_citations instead.
    force_refresh is for dev only (e.g. ?refresh=1).
    """
    raw = get_crossref_works_response(doi, force_refresh=force_refresh)
    if not raw:
        return None
    message = raw.get("message")
    if not message:
        logger.warning("Crossref API returned no message for DOI %s", doi)
        return None
    return crossref_response_from_message(message)


def get_crossref_works_response(doi: str, force_refresh: bool = False) -> dict | None:
    """
    Fetch the raw Crossref works API response for a DOI.
    Returns the full JSON body (status, message-type, message) or None on failure.
    HTTP responses are cached by the session (30 days); force_refresh uses a direct request.
    """
    doi = normalize_doi(doi)
    try:
        encoded_doi = quote(doi, safe="")
        url = f"{CROSSREF_WORKS}/{encoded_doi}"
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if force_refresh:
            resp = requests.get(url, headers=headers, timeout=30)
        else:
            resp = _crossref_session.get(url, headers=headers, timeout=30)
        if not resp.ok:
            logger.warning(
                "Crossref works API failed for DOI %s: HTTP %s %s",
                doi,
                resp.status_code,
                resp.reason or "",
            )
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        if doi not in _logged_failures:
            logger.warning("Failed to fetch Crossref works for DOI %s: %s", doi, e)
            _logged_failures.add(doi)
        return None
