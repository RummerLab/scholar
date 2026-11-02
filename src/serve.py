import hashlib
import json
import logging
import os
import re
from datetime import datetime
from email.utils import formatdate
from pathlib import Path

from flask import Flask, jsonify, make_response, request, send_from_directory

import src.cache_config  # noqa: F401 - configure HTTP cache before requests

from .doi_utils import normalize_doi
from .path_safety import safe_path_under
from .scholar_citations import (
    fetch_altmetric_score,
    fetch_crossref_for_api,
    fetch_google_scholar_citations,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False  # Preserve key order: doi first, then citations/score

SCHOLAR_DATA_DIR = os.environ.get("SCHOLAR_DATA_DIR", "scholar_data")
SCHOLAR_DATA_DIR_ABS = os.path.abspath(SCHOLAR_DATA_DIR)

# HTTP cache: clients revalidate after 1 day to pick up citation/score updates
DOI_CACHE_MAX_AGE = 86400  # 1 day


def _validated_scholar_id(scholar_id: str) -> str | None:
    """Return ``scholar_id`` if it is a safe basename segment, else ``None``."""
    if len(scholar_id) != 12 or not re.match(r"^[a-zA-Z0-9_-]+$", scholar_id):
        return None
    return scholar_id


def _scholar_data_path_for_id(scholar_id: str) -> Path | None:
    """Resolved path to ``<id>.json`` under ``SCHOLAR_DATA_DIR_ABS``, or ``None``."""
    vid = _validated_scholar_id(scholar_id)
    if not vid:
        return None
    return safe_path_under(SCHOLAR_DATA_DIR_ABS, f"{vid}.json")


def _load_scholar_data_or_error(scholar_id: str):
    """
    Load scholar JSON data from disk.
    Returns (data, None) on success, or (None, (body, status_code)) on error.
    """
    if not scholar_id:
        return None, ({"error": "Missing id"}, 400)
    p = _scholar_data_path_for_id(scholar_id)
    if not p:
        return None, ({"error": "Invalid id"}, 400)
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, ({"error": "Invalid scholar data"}, 500)
        return data, None
    except FileNotFoundError:
        return None, ({"error": "Author not found"}, 404)
    except json.JSONDecodeError:
        return None, ({"error": "Invalid scholar data"}, 500)


def _parse_pagination_args(
    default_limit: int = 50, max_limit: int = 200
) -> tuple[int, int] | tuple[None, dict]:
    try:
        limit = int(request.args.get("limit", str(default_limit)))
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return None, {"error": "Invalid pagination: limit/offset must be integers"}

    if limit < 0 or offset < 0:
        return None, {"error": "Invalid pagination: limit/offset must be non-negative"}
    if limit > max_limit:
        limit = max_limit
    return limit, offset


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy"}), 200


@app.route("/favicon.ico", methods=["GET"])
def favicon():
    """Serve favicon if present in project root."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    favicon_path = os.path.join(root, "favicon.ico")
    if os.path.isfile(favicon_path):
        return send_from_directory(root, "favicon.ico")
    return "", 204  # No content


@app.route("/", methods=["GET"])
def index():
    return "Welcome to the RummerLab API"


@app.route("/scholars", methods=["GET"])
def list_scholars():
    """List available scholar IDs with JSON data."""
    if not os.path.isdir(SCHOLAR_DATA_DIR_ABS):
        return jsonify({"scholars": []}), 200
    try:
        ids = [
            f.replace(".json", "") for f in os.listdir(SCHOLAR_DATA_DIR_ABS) if f.endswith(".json")
        ]
        return jsonify({"scholars": sorted(ids)}), 200
    except OSError:
        return jsonify({"error": "Could not list scholars"}), 500


@app.route("/scholar/<id>", methods=["GET"])
def get_scholar(id):
    """Get scholar data by ID."""
    data, err = _load_scholar_data_or_error(id)
    if err:
        body, status = err
        return jsonify(body), status

    # Optional: allow callers to request only certain parts to keep responses small.
    # Examples:
    # - /scholar/<id>?parts=profile
    parts_raw = request.args.get("parts") or request.args.get("fields")
    if not parts_raw:
        return jsonify(data)

    parts = {p.strip().lower() for p in parts_raw.split(",") if p.strip()}

    allowed = {"profile", "publications", "pubs", "all"}
    if not parts.issubset(allowed):
        return jsonify({"error": "Invalid parts; allowed: profile, publications, pubs, all"}), 400

    if "all" in parts:
        return jsonify(data)

    result: dict = {"id": id}

    if "publications" in parts or "pubs" in parts:
        result["publications"] = data.get("publications", [])

    if "profile" in parts or (parts - {"publications", "pubs"}):
        # Profile = everything except the large arrays unless explicitly requested.
        profile = dict(data)
        if "publications" not in parts and "pubs" not in parts:
            profile.pop("publications", None)
        result["profile"] = profile

    return jsonify(result)


@app.route("/scholar/<id>/gscholar", methods=["GET"])
def get_scholar_gscholar(id):
    """
    Get the Google-Scholar-derived profile data without large sub-resources.
    By default this excludes `publications` to keep the payload cache-friendly.
    """
    data, err = _load_scholar_data_or_error(id)
    if err:
        body, status = err
        return jsonify(body), status

    include_publications = request.args.get("include_publications") == "1"

    result = dict(data)
    if not include_publications:
        result.pop("publications", None)
    return jsonify(result)


@app.route("/scholar/<id>/publications", methods=["GET"])
def get_scholar_publications(id):
    """Get scholar publications only (paginated)."""
    data, err = _load_scholar_data_or_error(id)
    if err:
        body, status = err
        return jsonify(body), status

    pubs = data.get("publications", []) or []
    page = _parse_pagination_args(default_limit=50, max_limit=200)
    if page[0] is None:
        return jsonify(page[1]), 400
    limit, offset = page
    sliced = pubs[offset : offset + limit]
    return jsonify(
        {
            "id": id,
            "total": len(pubs),
            "limit": limit,
            "offset": offset,
            "publications": sliced,
        }
    )


# Basic DOI pattern: prefix 10. (registry) / suffix (no strict length)
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+$")


def _normalize_doi_for_api(doi: str) -> str:
    """Normalize DOI: unquote (handles single and multiple URL-encoding) and strip."""
    return normalize_doi(doi)


def _doi_cache_headers(last_fetch: str, doi: str) -> dict[str, str]:
    """Return Cache-Control, Last-Modified, and ETag for DOI responses (1-day client cache)."""
    try:
        dt = datetime.fromisoformat(last_fetch.replace("Z", "+00:00"))
        last_modified = formatdate(dt.timestamp(), usegmt=True)
    except (ValueError, TypeError):
        last_modified = formatdate(None, usegmt=True)
    etag = hashlib.sha256(f"{doi}:{last_fetch}".encode()).hexdigest()
    return {
        "Cache-Control": f"public, max-age={DOI_CACHE_MAX_AGE}, must-revalidate",
        "Last-Modified": last_modified,
        "ETag": f'"{etag}"',
    }


def _etag_matches_request(etag: str, request_etag: str | None) -> bool:
    """True if request's If-None-Match matches our ETag (allows 304)."""
    if not request_etag:
        return False
    # If-None-Match can be "etag1", "etag1", "etag2" or *
    if request_etag.strip() == "*":
        return True
    return etag.strip() in [e.strip().strip('"') for e in request_etag.split(",")]


def _validate_doi_for_api(doi: str) -> tuple[str | None, str | None]:
    """
    Normalize and validate DOI for API use.
    Returns (normalized_doi, None) if valid, or (None, error_message) if invalid.
    """
    normalized = _normalize_doi_for_api(doi)
    if not normalized:
        return None, "Invalid DOI: empty"
    if "/" not in normalized:
        return None, "Invalid DOI: must contain a slash (e.g. 10.1234/example)"
    if "%" in normalized:
        return None, "Invalid DOI: malformed encoding (use 10.xxxx/suffix or single-encoded path)"
    if any(segment in {".", ".."} for segment in normalized.split("/")):
        return None, "Invalid DOI: path traversal segments are not allowed"
    if not _DOI_PATTERN.match(normalized):
        return None, "Invalid DOI: must match 10.xxxx/suffix format"
    return normalized, None


@app.route("/robots.txt", methods=["GET"])
def robots_txt():
    """Serve robots.txt to avoid 404s from crawlers and reduce noisy log traffic."""
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/altmetric/<path:doi>", methods=["GET"])
def get_altmetric(doi: str):
    """
    Fetch Altmetric data for a DOI. Cached for 2 weeks.
    Returns full Altmetric data (score, title, authors, counts, etc.).
    Returns 401 if Crossref does not list Rummer, Bergseth, or Wu.
    Query param ?refresh=1 (dev only) forces a fresh fetch.
    """
    normalized_doi, err = _validate_doi_for_api(doi)
    if err:
        resp = jsonify({"error": err})
        resp.headers["Cache-Control"] = "no-store"
        return resp, 400
    doi = normalized_doi
    force_refresh = request.args.get("refresh") == "1"  # dev only
    result = fetch_altmetric_score(doi, force_refresh=force_refresh)
    if not result.found:
        logger.warning(
            "Altmetric API returning 401 for DOI %s (publication not found or author not in allowlist)",
            doi,
        )
        body = {"error": "Publication not found or author not in allowlist"}
        if result.error_reason:
            body["reason"] = result.error_reason
        if result.last_fetch is not None:
            body["last_fetch"] = result.last_fetch
        if result.last_successful_fetch is not None:
            body["last_successful_fetch"] = result.last_successful_fetch
        if result.last_fetched_result is not None:
            body["last_fetched_result"] = result.last_fetched_result
        resp = jsonify(body)
        resp.headers["Cache-Control"] = "no-store"
        return resp, 401
    headers = _doi_cache_headers(result.last_fetch, result.doi)
    etag = hashlib.sha256(f"{result.doi}:{result.last_fetch}".encode()).hexdigest()
    if _etag_matches_request(etag, request.headers.get("If-None-Match")):
        resp = make_response("", 304)
        resp.headers.update(headers)
        return resp
    data = result.details if result.details else {"doi": result.doi, "score": result.score}
    data = {**data, "last_fetch": result.last_fetch}
    if result.last_successful_fetch is not None:
        data["last_successful_fetch"] = result.last_successful_fetch
    if result.last_fetched_result is not None:
        data["last_fetched_result"] = result.last_fetched_result
    resp = jsonify(data)
    resp.headers.update(headers)
    return resp


@app.route("/google-citations/<path:doi>", methods=["GET"])
def get_google_citations(doi: str):
    """
    Fetch Google Scholar citation count for a DOI. Cached for 2 weeks.
    Returns 401 if Crossref does not list Rummer, Bergseth, or Wu.
    Query param ?refresh=1 (dev only) forces a fresh fetch.
    """
    normalized_doi, err = _validate_doi_for_api(doi)
    if err:
        resp = jsonify({"error": err})
        resp.headers["Cache-Control"] = "no-store"
        return resp, 400
    doi = normalized_doi
    force_refresh = request.args.get("refresh") == "1"  # dev only
    result = fetch_google_scholar_citations(doi, force_refresh=force_refresh)
    if not result.found:
        logger.warning(
            "Google citations API returning 401 for DOI %s (publication not found or author not in allowlist)",
            doi,
        )
        body = {"error": "Publication not found or author not in allowlist"}
        if result.error_reason:
            body["reason"] = result.error_reason
        if result.last_fetch is not None:
            body["last_fetch"] = result.last_fetch
        if result.last_successful_fetch is not None:
            body["last_successful_fetch"] = result.last_successful_fetch
        if result.last_fetched_result is not None:
            body["last_fetched_result"] = result.last_fetched_result
        resp = jsonify(body)
        resp.headers["Cache-Control"] = "no-store"
        return resp, 401
    headers = _doi_cache_headers(result.last_fetch, result.doi)
    etag = hashlib.sha256(f"{result.doi}:{result.last_fetch}".encode()).hexdigest()
    if _etag_matches_request(etag, request.headers.get("If-None-Match")):
        resp = make_response("", 304)
        resp.headers.update(headers)
        return resp
    data = {
        "doi": result.doi,
        "citations": result.citations,
        "last_fetch": result.last_fetch,
    }
    if result.last_successful_fetch is not None:
        data["last_successful_fetch"] = result.last_successful_fetch
    if result.last_fetched_result is not None:
        data["last_fetched_result"] = result.last_fetched_result
    if result.warning:
        data["warning"] = result.warning
    resp = jsonify(data)
    resp.headers.update(headers)
    return resp


@app.route("/crossref/<path:doi>", methods=["GET"])
def get_crossref(doi: str):
    """
    Fetch Crossref works API data for a DOI. Cached for 1 month.
    Returns the full Crossref API response (status, message-type, message).
    Query param ?refresh=1 (dev only) forces a fresh fetch.
    """
    normalized_doi, err = _validate_doi_for_api(doi)
    if err:
        resp = jsonify({"error": err})
        resp.headers["Cache-Control"] = "no-store"
        return resp, 400
    doi = normalized_doi
    force_refresh = request.args.get("refresh") == "1"  # dev only
    result = fetch_crossref_for_api(doi, force_refresh=force_refresh)
    if not result.found:
        logger.warning("Crossref API returning 404 for DOI %s: %s", doi, result.error_reason)
        body = {"error": "DOI not found or Crossref API error"}
        if result.error_reason:
            body["reason"] = result.error_reason
        if result.last_fetch is not None:
            body["last_fetch"] = result.last_fetch
        if result.last_successful_fetch is not None:
            body["last_successful_fetch"] = result.last_successful_fetch
        if result.last_fetched_result is not None:
            body["last_fetched_result"] = result.last_fetched_result
        resp = jsonify(body)
        resp.headers["Cache-Control"] = "no-store"
        return resp, 404
    headers = _doi_cache_headers(result.last_fetch or "", result.doi)
    etag = hashlib.sha256(f"{result.doi}:{result.last_fetch}".encode()).hexdigest()
    if _etag_matches_request(etag, request.headers.get("If-None-Match")):
        resp = make_response("", 304)
        resp.headers.update(headers)
        return resp
    data = dict(result.data) if result.data else {}
    data["last_fetch"] = result.last_fetch
    if result.last_successful_fetch is not None:
        data["last_successful_fetch"] = result.last_successful_fetch
    if result.last_fetched_result is not None:
        data["last_fetched_result"] = result.last_fetched_result
    resp = jsonify(data)
    resp.headers.update(headers)
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
