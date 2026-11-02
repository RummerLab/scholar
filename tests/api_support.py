"""Synthetic website contract data; never read production data or credentials."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHOLARS = (
    ("ynWS968AAAAJ", "Jodie L. Rummer", "Rummer"),
    ("cXDRggIAAAAJ", "Nicholas C. Wu", "Wu"),
    ("g9B1IoQAAAAJ", "Brock Bergseth", "Bergseth"),
)


def author_data(scholar_id: str, name: str, family: str) -> dict:
    """Return representative scholarly output with nonzero website metrics."""
    return {
        "scholar_id": scholar_id,
        "name": name,
        "citedby": 123,
        "citedby5y": 45,
        "hindex": 12,
        "hindex5y": 8,
        "i10index": 10,
        "i10index5y": 6,
        "cites_per_year": {"2025": 42},
        "coauthors": [{"scholar_id": "ynWS968AAAAJ", "name": "Jodie L. Rummer"}],
        "publications": [
            {
                "author_pub_id": f"{scholar_id}:paper{i}",
                "bib": {"title": f"Synthetic paper {i}", "pub_year": "2025", "author": name},
                "doi": f"10.1234/{family.lower()}-{i}",
                "num_citations": i,
            }
            for i in range(3)
        ],
    }


def seed_api_data(root: Path) -> None:
    """Write isolated profile and fresh DOI cache fixtures for a real server."""
    data_dir = root / "scholar_data"
    cache_dir = root / "doi_metrics"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    for scholar_id, name, family in SCHOLARS:
        author = author_data(scholar_id, name, family)
        (data_dir / f"{scholar_id}.json").write_text(json.dumps(author), encoding="utf-8")
        for publication in author["publications"]:
            doi = publication["doi"]
            values = {
                "scholar": {"citations": 42},
                "altmetric": {"score": 7, "details": {"doi": doi, "score": 7}},
                "crossref": {
                    "data": {
                        "status": "ok",
                        "message-type": "work",
                        "message": {"DOI": doi, "is-referenced-by-count": 23},
                    }
                },
            }
            for prefix, value in values.items():
                cache = {
                    "doi": doi,
                    "found": True,
                    "fetched_at": now.isoformat(),
                    "expires_at": (now + timedelta(days=1)).isoformat(),
                    "last_successful_fetch": now.isoformat(),
                    "last_fetched_result": "success",
                    **value,
                }
                (cache_dir / f"{prefix}_{doi.replace('/', '_')}.json").write_text(
                    json.dumps(cache), encoding="utf-8"
                )
