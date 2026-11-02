#!/usr/bin/env python3
"""
Remove DOIs from scholar_data (publication entries) and from the DOI metrics cache.

Usage:
  python scripts/remove_dois_from_data_and_cache.py 10.1093/conphys/coab030 10.14288/1.0071389

Run from project root or via: docker exec scholar_web python scripts/remove_dois_from_data_and_cache.py DOI ...
"""

import json
import os
import sys

from common import PROJECT_ROOT, setup_script  # noqa: E402

setup_script()

from src.doi_utils import normalize_doi  # noqa: E402
from src.path_safety import safe_path_under  # noqa: E402
from src.scholar_citations import (  # noqa: E402
    CACHE_DIR,
    _doi_from_cache_file_or_name,
    _listdir_cache_json_path,
)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: remove_dois_from_data_and_cache.py DOI [DOI ...]", file=sys.stderr)
        return 1

    to_remove = set()
    for raw in sys.argv[1:]:
        doi = (raw or "").strip()
        if doi:
            to_remove.add(normalize_doi(doi))

    if not to_remove:
        print("No DOIs to remove.", file=sys.stderr)
        return 1

    # Scholar data directory
    data_dir = os.environ.get("SCHOLAR_DATA_DIR", "scholar_data")
    if not os.path.isabs(data_dir):
        data_dir = os.path.join(PROJECT_ROOT, data_dir)
    data_dir = os.path.abspath(data_dir)

    removed_from_data = 0
    data_dir_abs = os.path.abspath(data_dir)
    if os.path.isdir(data_dir_abs):
        for name in os.listdir(data_dir_abs):
            if not name.endswith(".json"):
                continue
            # Match serve.py: only open basenames validated before join.
            safe_name = os.path.basename(name)
            if safe_name != name or not safe_name.endswith(".json"):
                continue
            path = safe_path_under(data_dir_abs, safe_name)
            if path is None:
                continue
            try:
                with path.open(encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            pubs = data.get("publications") or []
            new_pubs = [
                p for p in pubs if normalize_doi((p.get("doi") or "").strip()) not in to_remove
            ]
            dropped = len(pubs) - len(new_pubs)
            if dropped:
                data["publications"] = new_pubs
                try:
                    with path.open("w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    removed_from_data += dropped
                    print(f"  {name}: removed {dropped} publication(s)")
                except OSError as e:
                    print(f"  {name}: failed to write: {e}", file=sys.stderr)
    else:
        print(f"Scholar data dir not found: {data_dir}", file=sys.stderr)

    # DOI metrics cache (scholar_*.json, altmetric_*.json, crossref_*.json)
    removed_from_cache = 0
    if os.path.isdir(CACHE_DIR):
        for name in os.listdir(CACHE_DIR):
            if not (
                name.startswith(("scholar_", "altmetric_", "crossref_")) and name.endswith(".json")
            ):
                continue
            p = _listdir_cache_json_path(name)
            if p is None:
                continue
            prefix = name.split("_", 1)[0]
            if _doi_from_cache_file_or_name(p, name, prefix) not in to_remove:
                continue
            try:
                p.unlink()
                removed_from_cache += 1
                print(f"  removed cache: {p.name}")
            except OSError as e:
                print(f"  failed to remove {p}: {e}", file=sys.stderr)

    print(
        f"Done: {removed_from_data} publication(s) removed from data, {removed_from_cache} cache file(s) removed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
