"""
DOI metrics: Altmetric score and Google Scholar citations.

Fetches and caches results for two weeks. Uses Crossref first to verify that
Rummer, Bergseth, or Wu are authors; returns 401 if not. No page-content
author check after fetching.
"""

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote

import requests
import requests.adapters
from bs4 import BeautifulSoup

from .altmetric import fetch_altmetric_details
from .crossref import (
    CrossrefResponse,
    crossref_response_from_message,
    get_crossref_works_response,
)
from .doi_utils import normalize_doi
from .path_safety import safe_path_under
from .proxy_config import get_request_proxy_chain, get_request_proxy_chain_summary

logger = logging.getLogger(__name__)

# Scholar uses an uncached session so each proxy attempt gets a real request.
# requests_cache.install_cache() replaces requests.Session with CachedSession,
# so every proxy would otherwise see the same cached response (keyed by URL only).
try:
    import requests_cache

    _OriginalSession = getattr(requests_cache, "OriginalSession", requests.Session)
except ImportError:
    _OriginalSession = requests.Session
_scholar_session = _OriginalSession()
_scholar_session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
_scholar_session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))


def _invalidate_scholar_url_from_http_cache(url: str) -> None:
    """Remove this Scholar URL from the global HTTP cache so the next request is fresh."""
    try:
        import requests_cache

        if not requests_cache.is_installed():
            return
        cache = requests_cache.get_cache()
        if cache is None:
            return
        from requests import Request

        key = requests_cache.create_key(Request("GET", url))
        cache.delete(key)
        logger.debug("Invalidated HTTP cache entry for Scholar URL (blocked response)")
    except Exception:  # noqa: S110
        pass


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
GOOGLE_SCHOLAR_BASE = "https://scholar.google.com/scholar"

ALLOWED_AUTHORS = ("Rummer", "Bergseth", "Wu")
CACHE_SECONDS = 14 * 24 * 60 * 60  # 2 weeks
CACHE_SECONDS_BLOCKED_OR_WARNING = 24 * 60 * 60  # 1 day when Scholar blocked or request failed
CACHE_SECONDS_CROSSREF = 30 * 24 * 60 * 60  # 1 month for Crossref API endpoint

# Result of last fetch; stored in cache for debugging and revalidation
FETCH_RESULT_SUCCESS = "success"
FETCH_RESULT_AUTHOR_NOT_ALLOWED = "author_not_allowed"
FETCH_RESULT_NOT_FOUND = "not_found"
FETCH_RESULT_BLOCKED = "blocked"
FETCH_RESULT_ERROR = "error"
CACHE_DIR = os.environ.get("DOI_METRICS_CACHE_DIR", "")
if not CACHE_DIR:
    base = os.environ.get("CACHE_DIR", "cache")
    CACHE_DIR = os.path.join(base, "doi_metrics")
CACHE_DIR = os.path.abspath(CACHE_DIR)


_CACHE_SAFE_STEM_PATTERN = r"[A-Za-z0-9_.%~()+,;-]+"
_CACHE_SAFE_STEM_RE = re.compile(rf"^{_CACHE_SAFE_STEM_PATTERN}$")
_CACHE_JSON_BASENAME_RE = re.compile(
    rf"^(?P<prefix>crossref|scholar|altmetric)_(?P<safe>{_CACHE_SAFE_STEM_PATTERN})\.json$"
)
_ALLOWED_CACHE_PREFIX = frozenset({"crossref", "scholar", "altmetric"})


def _doi_metrics_cache_file(doi: str, prefix: str) -> Path | None:
    """Resolved cache file path for a DOI + kind, or ``None`` if inputs are not cache-safe."""
    if prefix not in _ALLOWED_CACHE_PREFIX:
        return None
    doi_n = normalize_doi(doi)
    safe = _normalize_doi_for_cache(doi_n)
    if not _CACHE_SAFE_STEM_RE.fullmatch(safe):
        return None
    name = f"{prefix}_{safe}.json"
    if not _CACHE_JSON_BASENAME_RE.match(name):
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    return safe_path_under(CACHE_DIR, name)


def _listdir_cache_json_path(name: str) -> Path | None:
    """Resolve ``name`` under the DOI metrics cache root if it is a valid cache basename."""
    if not _CACHE_JSON_BASENAME_RE.match(name):
        return None
    return safe_path_under(CACHE_DIR, name)


def _normalize_doi_for_cache(doi: str) -> str:
    """Safe filename stem from DOI while preserving legacy simple filenames."""
    legacy_stem = normalize_doi(doi).replace("/", "_").replace(":", "_").strip()
    return quote(
        legacy_stem,
        safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-",
    )


def _read_cache_json_object(path: Path) -> dict | None:
    """Read a cache JSON file when it contains an object; treat other JSON as corrupt."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _normalized_doi_from_cache_data(data: dict) -> str | None:
    """Return a normalized cache DOI when the field is present and string-like."""
    doi = data.get("doi")
    if not isinstance(doi, str) or not doi.strip():
        return None
    return normalize_doi(doi)


def _parse_cache_datetime(value: object) -> datetime | None:
    """Parse cache timestamps and ignore malformed values."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now_for_comparison(dt: datetime) -> datetime:
    """Return a current datetime compatible with ``dt`` for comparisons."""
    return datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()


def _doi_from_cache_filename(name: str, expected_prefix: str) -> str | None:
    """Best-effort DOI recovery from cache filename for legacy or invalid JSON cache entries."""
    match = _CACHE_JSON_BASENAME_RE.match(name)
    if not match or match.group("prefix") != expected_prefix:
        return None
    stem = unquote(match.group("safe"))
    doi = stem.replace("_", "/")
    return normalize_doi(doi) if doi else None


def _doi_from_cache_file_or_name(path: Path, name: str, expected_prefix: str) -> str | None:
    """Read DOI from cache JSON, falling back to the legacy filename encoding."""
    data = _read_cache_json_object(path)
    if data is not None:
        doi = _normalized_doi_from_cache_data(data)
        if doi:
            return doi
    return _doi_from_cache_filename(name, expected_prefix)


def _authors_contain_allowed(
    authors: list[str] | None,
    author_families: list[str] | None = None,
) -> bool:
    """Return True if any author family name is Rummer, Bergseth, or Wu (or contained in full names)."""
    # Prefer explicit family names from Crossref (e.g. "Wu" from author[].family)
    if author_families:
        combined_families = " ".join(author_families).lower()
        if any(a.lower() in combined_families for a in ALLOWED_AUTHORS):
            return True
    if not authors:
        return False
    combined = " ".join(authors).lower()
    return any(a.lower() in combined for a in ALLOWED_AUTHORS)


def _read_cache(doi: str, prefix: str) -> tuple[dict | None, bool]:
    """Return (cached_value, is_expired). Cached value has 'value' and 'expires_at'."""
    p = _doi_metrics_cache_file(doi, prefix)
    if p is None or not p.is_file():
        return None, True
    try:
        data = _read_cache_json_object(p)
        if data is None:
            return None, True
        exp_dt = _parse_cache_datetime(data.get("expires_at"))
        if exp_dt is None:
            return None, True
        if _now_for_comparison(exp_dt) > exp_dt:
            return data, True
        return data, False
    except (OSError, TypeError):
        return None, True


def list_cached_successful_dois() -> set[str]:
    """Return DOIs that have successful scholar or altmetric cache (found=True, no warning)."""
    dois: set[str] = set()
    if not os.path.isdir(CACHE_DIR):
        return dois
    for prefix in ("scholar_", "altmetric_"):
        for name in os.listdir(CACHE_DIR):
            if not name.startswith(prefix) or not name.endswith(".json"):
                continue
            p = _listdir_cache_json_path(name)
            if p is None:
                continue
            data = _read_cache_json_object(p)
            if data is None:
                continue
            doi = _normalized_doi_from_cache_data(data)
            if data.get("found") and doi and not data.get("warning"):
                dois.add(doi)
    return dois


def list_cached_dois_with_scholar_cache() -> set[str]:
    """Return DOIs that have a scholar cache file (any result, including found=False). Used to avoid refetching 401s every run."""
    dois: set[str] = set()
    if not os.path.isdir(CACHE_DIR):
        return dois
    prefix = "scholar_"
    for name in os.listdir(CACHE_DIR):
        if not name.startswith(prefix) or not name.endswith(".json"):
            continue
        p = _listdir_cache_json_path(name)
        if p is None:
            continue
        doi = _doi_from_cache_file_or_name(p, name, "scholar")
        if doi:
            dois.add(doi)
    return dois


def list_cached_dois_with_warning() -> set[str]:
    """Return DOIs that have a scholar or altmetric cache entry with a warning (blocked/failed)."""
    dois: set[str] = set()
    if not os.path.isdir(CACHE_DIR):
        return dois
    for prefix in ("scholar_", "altmetric_"):
        for name in os.listdir(CACHE_DIR):
            if not name.startswith(prefix) or not name.endswith(".json"):
                continue
            p = _listdir_cache_json_path(name)
            if p is None:
                continue
            data = _read_cache_json_object(p)
            if data is None:
                continue
            doi = _normalized_doi_from_cache_data(data)
            if data.get("warning") and doi:
                dois.add(doi)
    return dois


def touch_scholar_cache(doi: str) -> bool:
    """
    Update fetched_at (and expires_at) in the scholar cache file to now.
    Used when revalidation runs but Scholar is skipped (blocked) so that
    last_fetch reflects "last run" rather than the previous fetch date.
    Returns True if the cache file was updated, False if no file exists.
    """
    doi = normalize_doi(doi)
    p = _doi_metrics_cache_file(doi, "scholar")
    if p is None or not p.is_file():
        return False
    data = _read_cache_json_object(p)
    if data is None:
        return False
    now = datetime.now()
    ttl = CACHE_SECONDS_BLOCKED_OR_WARNING if data.get("warning") else CACHE_SECONDS
    data["fetched_at"] = now.isoformat()
    data["expires_at"] = (now + timedelta(seconds=ttl)).isoformat()
    try:
        with p.open("w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        return False
    return True


def list_cached_successful_dois_older_than(seconds: int) -> set[str]:
    """Return DOIs that have successful scholar/altmetric cache (found=True, no warning) older than given seconds."""
    dois: set[str] = set()
    if not os.path.isdir(CACHE_DIR):
        return dois
    for prefix in ("scholar_", "altmetric_"):
        for name in os.listdir(CACHE_DIR):
            if not name.startswith(prefix) or not name.endswith(".json"):
                continue
            p = _listdir_cache_json_path(name)
            if p is None:
                continue
            try:
                data = _read_cache_json_object(p)
                if data is None:
                    continue
                doi = _normalized_doi_from_cache_data(data)
                if not data.get("found") or not doi or data.get("warning"):
                    continue
                fetched_dt = _parse_cache_datetime(data.get("fetched_at"))
                if fetched_dt is None:
                    continue
                age_seconds = _now_for_comparison(fetched_dt).timestamp() - fetched_dt.timestamp()
                if age_seconds >= seconds:
                    dois.add(doi)
            except (OSError, TypeError):
                continue
    return dois


def _write_cache(
    doi: str,
    prefix: str,
    value: dict,
    *,
    cache_seconds: int | None = None,
    previous_cached: dict | None = None,
) -> None:
    """
    Write cache entry. value should include last_fetched_result.
    When last_fetched_result != success, last_successful_fetch is preserved from previous_cached.
    """
    doi_key = normalize_doi(doi)
    now = datetime.now()
    ttl = cache_seconds if cache_seconds is not None else CACHE_SECONDS
    expires = now + timedelta(seconds=ttl)
    data = {
        **value,
        "expires_at": expires.isoformat(),
        "fetched_at": now.isoformat(),
        "doi": doi_key,
    }
    if data.get("last_fetched_result") == FETCH_RESULT_SUCCESS:
        data["last_successful_fetch"] = now.isoformat()
    elif previous_cached is not None:
        prev = previous_cached.get("last_successful_fetch")
        if prev:
            data["last_successful_fetch"] = prev
    p = _doi_metrics_cache_file(doi_key, prefix)
    if p is None:
        logger.warning(
            "Refusing to write DOI metrics cache (invalid doi/prefix): %s %s", doi_key, prefix
        )
        return
    try:
        with p.open("w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        logger.warning("Failed to write DOI metrics cache: %s", e)


def _parse_scholar_citations(soup: BeautifulSoup) -> tuple[int | None, bool]:
    """
    Extract citation count from a Scholar results page.
    Returns (citations, no_results): no_results is True when the page says
    "did not match any articles".
    """
    citations: int | None = None
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if text.startswith("Cited by"):
            m = re.search(r"Cited by (\d+)", text)
            if m:
                try:
                    citations = int(m.group(1))
                    break
                except ValueError:
                    pass

    no_results = "did not match any articles" in soup.get_text()
    # Result found but no "Cited by" link (e.g. new article) => treat as 0
    if citations is None and not no_results:
        citations = 0
    return citations, no_results


def _scholar_search(
    query: str, headers: dict, proxies: dict | None
) -> tuple[int | None, bool] | None:
    """
    Run one Google Scholar search and parse the result.
    Returns (citations, no_results) on success, or None if the request failed or was blocked.
    """
    url = f"{GOOGLE_SCHOLAR_BASE}?hl=en&as_sdt=0%2C5&q={quote(query)}&btnG="
    try:
        resp = _scholar_session.get(url, headers=headers, timeout=30, proxies=proxies)
    except requests.RequestException:
        raise
    if not resp.ok:
        logger.warning(
            "Google Scholar search failed (%s) for query: %.60s", resp.status_code, query
        )
        return None
    if _is_blocked_response(resp.text, resp.url or url):
        logger.warning("Google Scholar appears to be blocking requests (CAPTCHA/IP block)")
        logger.debug(
            "Blocked response: url=%s status=%s body_snippet=%.500s",
            resp.url,
            resp.status_code,
            (resp.text or "").strip()[:500],
        )
        _invalidate_scholar_url_from_http_cache(resp.url or url)
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    return _parse_scholar_citations(soup)


def _scholar_search_with_proxy_retries(query: str, headers: dict) -> tuple[int | None, bool] | None:
    """
    Try Google Scholar search: Tor proxy first (up to 3 attempts, same proxy),
    then each SOCKS5 proxy in turn. Returns first successful result or None.
    """
    proxies_list = get_request_proxy_chain()
    chain_summary = get_request_proxy_chain_summary()
    logger.info("Scholar proxy chain: %s (%d attempts)", chain_summary, len(proxies_list))
    if not proxies_list:
        logger.warning("Scholar proxy chain is empty; cannot query Google Scholar for %.60s", query)
        return None

    last_e: requests.RequestException | None = None
    got_http_response = False  # True if any attempt got HTTP (even blocked/non-200)
    for proxies in proxies_list:
        try:
            result = _scholar_search(query, headers, proxies)
            got_http_response = True
            if result is not None:
                return result
        except requests.RequestException as e:
            last_e = e
            logger.debug("Scholar search failed with proxy: %s", e)
            continue
    if last_e is not None and not got_http_response:
        logger.warning(
            "Scholar unreachable for query %.60s: all %d attempts failed with connection error (e.g. %s). Check TOR_PROXY reachability and SOCKS5_PROXIES.",
            query,
            len(proxies_list),
            last_e,
        )
    elif got_http_response:
        logger.warning(
            "Scholar returned blocked/CAPTCHA or non-200 for all %d proxies for query %.60s",
            len(proxies_list),
            query,
        )
    return None


def _is_blocked_response(html: str, url: str) -> bool:
    """Detect if Google Scholar has blocked the request (CAPTCHA / rate limit page)."""
    if "scholar.google.com" not in url:
        return True
    lower = html.lower()
    # Normal Scholar result pages (or consent/cookie wall) contain one of these; if present, not a block page
    _valid_signals = (
        "cited by",
        "did not match any articles",
        "your search",  # "Your search - ... - did not match any articles"
        "showing the best result",  # single-result layout
        "see all results",
        "related articles",
        "before you continue",  # consent/cookie wall
        "we use cookies",
    )
    if any(s in lower for s in _valid_signals):
        return False
    # Block signals (captcha/recaptcha can appear in scripts on normal pages, so only block
    # when we don't have valid result content above)
    if "captcha" in lower or "recaptcha" in lower:
        return True
    if any(
        x in lower
        for x in (
            "unusual traffic",
            "automated queries",
            "our systems have detected",
            "sorry, we have detected",
        )
    ):
        return True
    return False


def _last_fetch_from_cache(cached: dict, doi: str, prefix: str) -> str | None:
    """Last fetch timestamp from cache: use fetched_at if present, else file mtime."""
    fetched_at = cached.get("fetched_at")
    if isinstance(fetched_at, str) and fetched_at:
        return fetched_at
    p = _doi_metrics_cache_file(doi, prefix)
    if p is None:
        return None
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat()
    except OSError:
        return None


def get_crossref_metadata_cached(doi: str, force_refresh: bool = False) -> CrossrefResponse | None:
    """
    Get Crossref metadata (authors, title, etc.) for a DOI using the shared 1-month file cache.
    Used by Altmetric and Google Scholar so they reuse the same Crossref data as the /crossref
    endpoint instead of hitting the API separately. On cache miss, fetches from API and writes cache.
    """
    doi = normalize_doi(doi)
    cached, expired = _read_cache(doi, "crossref")
    if not force_refresh and cached is not None and not expired:
        if cached.get("found") and cached.get("data"):
            message = (cached.get("data") or {}).get("message")
            if message:
                return crossref_response_from_message(message)
        return None  # Cached "not found" or no message

    raw = get_crossref_works_response(doi, force_refresh=force_refresh)
    if not raw or raw.get("status") != "ok":
        return None
    message = raw.get("message")
    if not message:
        return None
    _write_cache(
        doi,
        "crossref",
        {"found": True, "data": raw, "last_fetched_result": FETCH_RESULT_SUCCESS},
        cache_seconds=CACHE_SECONDS_CROSSREF,
    )
    return crossref_response_from_message(message)


@dataclass
class AltmetricResult:
    doi: str
    score: int | None
    found: bool  # True if authors validated
    details: dict | None = None  # Full Altmetric data for API response
    last_fetch: str | None = None  # ISO timestamp when data was last fetched
    last_successful_fetch: str | None = None  # When we last had found=True
    last_fetched_result: str | None = None  # success | author_not_allowed | not_found | error
    error_reason: str | None = None  # When found=False: why (for API JSON)


def fetch_altmetric_score(doi: str, force_refresh: bool = False) -> AltmetricResult:
    """
    Fetch Altmetric data for a DOI. Cached for 2 weeks.
    Returns found=False (401) if Crossref does not list Rummer, Bergseth, or Wu.
    Uses Crossref first to verify authors; no page-content author check after fetch.
    force_refresh is for dev only (e.g. ?refresh=1); production/cron should not use it.
    """
    doi = normalize_doi(doi)
    cached, expired = _read_cache(doi, "altmetric")
    if not force_refresh and cached is not None and not expired:
        # If we have a cached 401, re-check Crossref; author may have been added or cache was stale
        if cached.get("found") is False and cached.get("error_reason") == "Author not in allowlist":
            crossref_recheck = get_crossref_metadata_cached(doi, force_refresh=False)
            if crossref_recheck and _authors_contain_allowed(
                crossref_recheck.authors, crossref_recheck.author_families
            ):
                # Crossref now has an allowed author; fall through to refetch and refresh cache
                pass
            else:
                return AltmetricResult(
                    doi=doi,
                    score=cached.get("score"),
                    found=False,
                    details=cached.get("details"),
                    last_fetch=_last_fetch_from_cache(cached, doi, "altmetric"),
                    last_successful_fetch=cached.get("last_successful_fetch"),
                    last_fetched_result=cached.get("last_fetched_result"),
                    error_reason=cached.get("error_reason"),
                )
        else:
            return AltmetricResult(
                doi=doi,
                score=cached.get("score"),
                found=cached.get("found", True),
                details=cached.get("details"),
                last_fetch=_last_fetch_from_cache(cached, doi, "altmetric"),
                last_successful_fetch=cached.get("last_successful_fetch"),
                last_fetched_result=cached.get("last_fetched_result"),
                error_reason=cached.get("error_reason"),
            )

    # Crossref (from shared 1-month cache): verify allowed author before fetching
    crossref = get_crossref_metadata_cached(doi, force_refresh=force_refresh)
    if not crossref or not _authors_contain_allowed(crossref.authors, crossref.author_families):
        if not crossref:
            reason = "Crossref returned no metadata (API error or DOI not found)"
            logger.warning("Altmetric 401 for DOI %s: %s", doi, reason)
        else:
            reason = "Author not in allowlist"
            logger.warning(
                "Altmetric 401 for DOI %s: %s. Crossref authors: %s",
                doi,
                reason,
                (crossref.authors or [])[:10],
            )
        now_iso = datetime.now().isoformat()
        result_code = FETCH_RESULT_AUTHOR_NOT_ALLOWED if crossref else FETCH_RESULT_NOT_FOUND
        _write_cache(
            doi,
            "altmetric",
            {
                "found": False,
                "score": None,
                "details": None,
                "error_reason": reason,
                "last_fetched_result": result_code,
            },
            previous_cached=cached if cached is not None else None,
        )
        return AltmetricResult(
            doi=doi,
            score=None,
            found=False,
            details=None,
            last_fetch=now_iso,
            last_fetched_result=result_code,
            error_reason=reason,
        )

    details = fetch_altmetric_details(doi)
    if details:
        details_dict = asdict(details)
        score = details.score
    else:
        details_dict = {"doi": doi, "score": None}
        score = None
    now_iso = datetime.now().isoformat()
    _write_cache(
        doi,
        "altmetric",
        {
            "found": True,
            "score": score,
            "details": details_dict,
            "last_fetched_result": FETCH_RESULT_SUCCESS,
        },
    )
    return AltmetricResult(
        doi=doi,
        score=score,
        found=True,
        details=details_dict,
        last_fetch=now_iso,
        last_successful_fetch=now_iso,
        last_fetched_result=FETCH_RESULT_SUCCESS,
    )


@dataclass
class ScholarCitationsResult:
    doi: str
    citations: int | None
    found: bool
    last_fetch: str | None = None  # ISO timestamp when data was last fetched
    last_successful_fetch: str | None = None  # When we last had found=True with citations
    last_fetched_result: str | None = (
        None  # success | author_not_allowed | not_found | blocked | error
    )
    error_reason: str | None = None  # When found=False: why (for API JSON)
    warning: str | None = None  # When found=True but citations=None: e.g. Scholar blocked


def fetch_google_scholar_citations(doi: str, force_refresh: bool = False) -> ScholarCitationsResult:
    """
    Fetch Google Scholar citation count for a DOI. Cached for 2 weeks.
    Searches by DOI first; if that returns no results, searches by publication title.
    Returns found=False (401) if Crossref does not list Rummer, Bergseth, or Wu.
    force_refresh is for dev only (e.g. ?refresh=1); production/cron should not use it.
    """
    doi = normalize_doi(doi)
    cached, expired = _read_cache(doi, "scholar")
    if not force_refresh and cached is not None and not expired:
        # If we have a cached 401, re-check Crossref; author may have been added or cache was stale
        if (
            cached.get("found") is False
            and cached.get("error_reason")
            and "allowlist" in (cached.get("error_reason") or "")
        ):
            crossref_recheck = get_crossref_metadata_cached(doi, force_refresh=False)
            if crossref_recheck and _authors_contain_allowed(
                crossref_recheck.authors, crossref_recheck.author_families
            ):
                pass  # Fall through to refetch and refresh cache
            else:
                return ScholarCitationsResult(
                    doi=doi,
                    citations=cached.get("citations"),
                    found=False,
                    last_fetch=_last_fetch_from_cache(cached, doi, "scholar"),
                    last_successful_fetch=cached.get("last_successful_fetch"),
                    last_fetched_result=cached.get("last_fetched_result"),
                    error_reason=cached.get("error_reason"),
                    warning=cached.get("warning"),
                )
        else:
            return ScholarCitationsResult(
                doi=doi,
                citations=cached.get("citations"),
                found=cached.get("found", True),
                last_fetch=_last_fetch_from_cache(cached, doi, "scholar"),
                last_successful_fetch=cached.get("last_successful_fetch"),
                last_fetched_result=cached.get("last_fetched_result"),
                error_reason=cached.get("error_reason"),
                warning=cached.get("warning"),
            )

    # Crossref (from shared 1-month cache): verify allowed author and get title for fallback
    crossref = get_crossref_metadata_cached(doi, force_refresh=force_refresh)
    if not crossref or not _authors_contain_allowed(crossref.authors, crossref.author_families):
        if not crossref:
            reason = "Crossref returned no metadata (API error or DOI not found)"
            logger.warning("Google Scholar 401 for DOI %s: %s", doi, reason)
        else:
            reason = "Author not in allowlist (Rummer, Bergseth, or Wu required)"
            logger.warning(
                "Google Scholar 401 for DOI %s: %s. Crossref authors: %s",
                doi,
                reason,
                (crossref.authors or [])[:10],
            )
        now_iso = datetime.now().isoformat()
        result_code = FETCH_RESULT_AUTHOR_NOT_ALLOWED if crossref else FETCH_RESULT_NOT_FOUND
        _write_cache(
            doi,
            "scholar",
            {
                "found": False,
                "citations": None,
                "error_reason": reason,
                "last_fetched_result": result_code,
            },
            previous_cached=cached if cached is not None else None,
        )
        return ScholarCitationsResult(
            doi=doi,
            citations=None,
            found=False,
            last_fetch=now_iso,
            last_fetched_result=result_code,
            error_reason=reason,
        )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://scholar.google.com/",
    }

    try:
        # 1. Search by DOI (try each proxy in turn if one is offline or blocked)
        result = _scholar_search_with_proxy_retries(doi, headers)
        if result is None:
            # Scholar blocked on all proxies; Crossref already passed so found=True
            warning = "Google Scholar blocked requests on all proxies (CAPTCHA/IP block)"
            now_iso = datetime.now().isoformat()
            if cached is not None and cached.get("found", True):
                # Keep existing data but update fetched_at so last_fetch reflects this run
                _write_cache(
                    doi,
                    "scholar",
                    {
                        "found": True,
                        "citations": cached.get("citations"),
                        "warning": cached.get("warning") or warning,
                        "last_fetched_result": FETCH_RESULT_BLOCKED,
                    },
                    cache_seconds=CACHE_SECONDS_BLOCKED_OR_WARNING,
                    previous_cached=cached,
                )
                return ScholarCitationsResult(
                    doi=doi,
                    citations=cached.get("citations"),
                    found=True,
                    last_fetch=now_iso,
                    last_successful_fetch=cached.get("last_successful_fetch"),
                    last_fetched_result=FETCH_RESULT_BLOCKED,
                    warning=cached.get("warning") or warning,
                )
            _write_cache(
                doi,
                "scholar",
                {
                    "found": True,
                    "citations": None,
                    "warning": warning,
                    "last_fetched_result": FETCH_RESULT_BLOCKED,
                },
                cache_seconds=CACHE_SECONDS_BLOCKED_OR_WARNING,
                previous_cached=cached if cached is not None else None,
            )
            return ScholarCitationsResult(
                doi=doi,
                citations=None,
                found=True,
                last_fetch=now_iso,
                last_successful_fetch=cached.get("last_successful_fetch") if cached else None,
                last_fetched_result=FETCH_RESULT_BLOCKED,
                warning=warning,
            )

        citations, no_results = result

        # 2. If DOI search had no results, search by publication title
        if citations is None and no_results and crossref.title:
            title_query = crossref.title.strip()
            if title_query:
                title_result = _scholar_search_with_proxy_retries(title_query, headers)
                if title_result is not None and title_result[0] is not None:
                    citations = title_result[0]
                    logger.info(
                        "Scholar DOI search had no results; got citations via title: %.60s",
                        title_query,
                    )

        now_iso = datetime.now().isoformat()
        _write_cache(
            doi,
            "scholar",
            {"found": True, "citations": citations, "last_fetched_result": FETCH_RESULT_SUCCESS},
        )
        return ScholarCitationsResult(
            doi=doi,
            citations=citations,
            found=True,
            last_fetch=now_iso,
            last_successful_fetch=now_iso,
            last_fetched_result=FETCH_RESULT_SUCCESS,
        )

    except requests.RequestException as e:
        logger.warning("Failed to scrape Google Scholar citations for DOI %s: %s", doi, e)
        now_iso = datetime.now().isoformat()
        warning = "Google Scholar request failed (network or proxy error)"
        prev_cached, _ = _read_cache(doi, "scholar")
        previous_citations = (
            prev_cached.get("citations")
            if prev_cached is not None and prev_cached.get("found", True)
            else None
        )
        previous_successful_fetch = (
            prev_cached.get("last_successful_fetch") if prev_cached is not None else None
        )
        _write_cache(
            doi,
            "scholar",
            {
                "found": True,
                "citations": previous_citations,
                "warning": warning,
                "last_fetched_result": FETCH_RESULT_ERROR,
            },
            cache_seconds=CACHE_SECONDS_BLOCKED_OR_WARNING,
            previous_cached=prev_cached,
        )
        return ScholarCitationsResult(
            doi=doi,
            citations=previous_citations,
            found=True,
            last_fetch=now_iso,
            last_successful_fetch=previous_successful_fetch,
            last_fetched_result=FETCH_RESULT_ERROR,
            warning=warning,
        )


@dataclass
class CrossrefResult:
    """Result of fetching Crossref works API for the public endpoint."""

    doi: str
    found: bool
    data: dict | None = None  # Full Crossref API response (status, message-type, message)
    last_fetch: str | None = None
    last_successful_fetch: str | None = None
    last_fetched_result: str | None = None  # success | not_found | error
    error_reason: str | None = None


def fetch_crossref_for_api(doi: str, force_refresh: bool = False) -> CrossrefResult:
    """
    Fetch Crossref works API data for a DOI. Cached for 1 month.
    Returns the full API response (status, message-type, message) for the /crossref/<doi> endpoint.
    """
    doi = normalize_doi(doi)
    cached, expired = _read_cache(doi, "crossref")
    if not force_refresh and cached is not None and not expired:
        return CrossrefResult(
            doi=doi,
            found=cached.get("found", True),
            data=cached.get("data"),
            last_fetch=_last_fetch_from_cache(cached, doi, "crossref"),
            last_successful_fetch=cached.get("last_successful_fetch"),
            last_fetched_result=cached.get("last_fetched_result"),
            error_reason=cached.get("error_reason"),
        )

    raw = get_crossref_works_response(doi, force_refresh=force_refresh)
    now_iso = datetime.now().isoformat()
    if not raw or raw.get("status") != "ok":
        reason = "Crossref API returned no data or non-OK status"
        if not raw:
            reason = "Crossref API error or DOI not found"
        result_code = FETCH_RESULT_ERROR if not raw else FETCH_RESULT_NOT_FOUND
        _write_cache(
            doi,
            "crossref",
            {
                "found": False,
                "data": None,
                "error_reason": reason,
                "last_fetched_result": result_code,
            },
            cache_seconds=CACHE_SECONDS_CROSSREF,
            previous_cached=cached if cached is not None else None,
        )
        return CrossrefResult(
            doi=doi,
            found=False,
            data=None,
            last_fetch=now_iso,
            last_fetched_result=result_code,
            error_reason=reason,
        )

    _write_cache(
        doi,
        "crossref",
        {"found": True, "data": raw, "last_fetched_result": FETCH_RESULT_SUCCESS},
        cache_seconds=CACHE_SECONDS_CROSSREF,
    )
    return CrossrefResult(
        doi=doi,
        found=True,
        data=raw,
        last_fetch=now_iso,
        last_successful_fetch=now_iso,
        last_fetched_result=FETCH_RESULT_SUCCESS,
    )
