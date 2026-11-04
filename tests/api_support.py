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
        "coauthors": [
            {
                "scholar_id": "ynWS968AAAAJ",
                "name": "Jodie L. Rummer",
                "affiliation": "James Cook University",
            }
        ],
        "publications": [
            {
                "author_pub_id": f"{scholar_id}:paper{i}",
                "bib": {
                    "title": f"Synthetic paper {i}",
                    "pub_year": "2025",
                    "author": name,
                    "journal": "Synthetic Journal",
                },
                "doi": f"10.1234/{family.lower()}-{i}",
                "num_citations": i,
            }
            for i in range(3)
        ],
    }


def _publication_title(family: str, index: int = 0) -> str:
    return f"Synthetic paper {index}"


def altmetric_details(doi: str, family: str, name: str, index: int = 0) -> dict:
    """Website-facing Altmetric payload fields (RummerLab score + Wu Lab metadata)."""
    title = _publication_title(family, index)
    return {
        "doi": doi,
        "title": title,
        "journal": "Synthetic Journal",
        "authors": [name],
        "year": 2025,
        "url": f"https://doi.org/{doi}",
        "score": 7,
        "cited_by_posts_count": 3,
        "cited_by_accounts_count": 2,
        "published_text": "January 2025",
        "altmetric_id": "12345678",
    }


def crossref_message(doi: str, family: str, name: str, index: int = 0) -> dict:
    """Crossref message fields parsed by Nicholas-Wu-Lab (and usable by RummerLab)."""
    given = name.rsplit(" ", 1)[0]
    return {
        "DOI": doi,
        "title": [_publication_title(family, index)],
        "container-title": ["Synthetic Journal"],
        "author": [{"given": given, "family": family}],
        "issued": {"date-parts": [[2025, 1, 15]]},
        "URL": f"https://doi.org/{doi}",
        "is-referenced-by-count": 23,
    }


def assert_website_profile_contract(profile: dict, scholar_id: str, name: str) -> None:
    """Fail if /gscholar omits fields RummerLab reads."""
    assert profile["scholar_id"] == scholar_id
    assert profile["name"] == name
    assert profile["citedby"] == 123
    assert profile["citedby5y"] == 45
    assert profile["hindex"] == 12
    assert profile["hindex5y"] == 8
    assert profile["i10index"] == 10
    assert profile["i10index5y"] == 6
    assert "publications" not in profile
    assert profile["coauthors"]
    coauthor = profile["coauthors"][0]
    assert coauthor["scholar_id"] == "ynWS968AAAAJ"
    assert coauthor["name"] == "Jodie L. Rummer"
    assert coauthor["affiliation"] == "James Cook University"


def assert_website_publications_contract(page: dict, scholar_id: str, family: str) -> None:
    """Fail if /publications omits fields RummerLab reads."""
    assert page["id"] == scholar_id
    assert page["total"] == 3
    assert "limit" in page and "offset" in page
    assert page["publications"]
    publication = page["publications"][0]
    assert publication["doi"] == f"10.1234/{family.lower()}-0"
    assert publication["num_citations"] == 0
    assert publication["bib"]["title"] == "Synthetic paper 0"


def assert_website_metric_contract(
    route: str, body: dict, doi: str, family: str, name: str
) -> None:
    """Fail if a DOI metric response breaks RummerLab / Wu Lab consumer fields."""
    if route == "crossref":
        message = body["message"]
        expected = crossref_message(doi, family, name)
        assert message["DOI"] == expected["DOI"]
        assert message["title"] == expected["title"]
        assert message["container-title"] == expected["container-title"]
        assert message["author"] == expected["author"]
        assert message["issued"] == expected["issued"]
        assert message["URL"] == expected["URL"]
        assert message["is-referenced-by-count"] == 23
        return

    if route == "altmetric":
        expected = altmetric_details(doi, family, name)
        for key, value in expected.items():
            assert body[key] == value, key
        return

    assert route == "google-citations"
    assert body["doi"] == doi
    assert body["citations"] == 42


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
        for index, publication in enumerate(author["publications"]):
            doi = publication["doi"]
            values = {
                "scholar": {"citations": 42},
                "altmetric": {
                    "score": 7,
                    "details": altmetric_details(doi, family, name, index),
                },
                "crossref": {
                    "data": {
                        "status": "ok",
                        "message-type": "work",
                        "message": crossref_message(doi, family, name, index),
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
