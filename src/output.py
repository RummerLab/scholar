"""Author data loading, saving, and schema management."""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .path_safety import safe_path_under

SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)

# Same character class as ``serve._validated_scholar_id`` (length may vary for tests).
_SCHOLAR_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def author_json_path(scholar_id: str, data_dir: str) -> Path | None:
    """Resolved ``<scholar_id>.json`` under ``data_dir``, or ``None`` if unsafe."""
    if not scholar_id or not _SCHOLAR_ID_RE.fullmatch(scholar_id):
        return None
    return safe_path_under(os.path.abspath(data_dir), f"{scholar_id}.json")


def _require_author_json_path(scholar_id: str, data_dir: str) -> Path:
    path = author_json_path(scholar_id, data_dir)
    if path is None:
        raise ValueError(f"Invalid scholar_id for output path: {scholar_id!r}")
    return path


def load_author(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Load author data from JSON file. Returns None if file does not exist or is invalid."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load author data from %s: %s", path, e)
        return None


def load_author_by_id(scholar_id: str, data_dir: str) -> dict[str, Any] | None:
    """Load ``<scholar_id>.json`` under ``data_dir``; raise ``ValueError`` for an unsafe id."""
    return load_author(_require_author_json_path(scholar_id, data_dir))


def save_author(
    author: dict[str, Any], path: str | os.PathLike[str], *, bump_last_fetched: bool = True
) -> None:
    """Save author data to JSON with schema_version and optionally ``last_fetched``."""
    author["schema_version"] = SCHEMA_VERSION
    if bump_last_fetched:
        author["last_fetched"] = datetime.now().isoformat()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(author, f, indent=4)
    logger.debug("Saved author data to %s", path)


def save_author_by_id(
    author: dict[str, Any],
    scholar_id: str,
    data_dir: str,
    *,
    bump_last_fetched: bool = True,
) -> Path:
    """Save ``<scholar_id>.json`` under ``data_dir`` and return its path.

    Raises ``ValueError`` for an unsafe ``scholar_id``.
    """
    path = _require_author_json_path(scholar_id, data_dir)
    save_author(author, path, bump_last_fetched=bump_last_fetched)
    return path


def is_fresh(last_fetched: str | None, fresh_seconds: int) -> bool:
    """Return True if last_fetched is within fresh_seconds of now."""
    if not last_fetched:
        return False
    try:
        dt = datetime.fromisoformat(last_fetched.replace("Z", "+00:00"))
        # Handle naive datetime (local time)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        age = (datetime.now(dt.tzinfo) - dt).total_seconds()
        return 0 <= age <= fresh_seconds
    except (ValueError, TypeError):
        return False


def get_last_successful_indices(data: dict[str, Any]) -> dict[str, int]:
    """Extract last successful indices for resume support."""
    return {
        "coauthor": data.get("_last_successful_coauthor_index", -1),
        "publication": data.get("_last_successful_publication_index", -1),
    }


def set_last_successful_index(author: dict[str, Any], phase: str, index: int) -> None:
    """Store last successful index for resume support."""
    key = f"_last_successful_{phase}_index"
    author[key] = index
