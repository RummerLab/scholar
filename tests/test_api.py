"""Website-facing Flask contracts, using real files/cache and isolated upstreams."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest

from src import scholar_citations as metrics
from src import serve
from src.crossref import crossref_response_from_message
from src.output import save_author_by_id
from tests.api_support import SCHOLARS, author_data, seed_api_data


@pytest.fixture
def client(monkeypatch, tmp_path):
    seed_api_data(tmp_path)
    monkeypatch.setattr(serve, "SCHOLAR_DATA_DIR_ABS", str(tmp_path / "scholar_data"))
    monkeypatch.setattr(metrics, "CACHE_DIR", str(tmp_path / "doi_metrics"))
    monkeypatch.setitem(serve.app.config, "TESTING", True)

    def unexpected_network(*args, **kwargs):
        pytest.fail("API contract tests must not contact external services")

    monkeypatch.setattr("socket.socket.connect", unexpected_network)
    with serve.app.test_client() as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path,status", [("/", 200), ("/health", 200), ("/robots.txt", 200), ("/favicon.ico", 200)]
)
def test_operational_routes(client, path, status):
    response = client.get(path)
    assert response.status_code == status
    if path == "/health":
        assert response.json == {"status": "healthy"}


def test_humans_txt_serves_project_credits(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    response = client.get("/humans.txt")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert "charset=utf-8" in response.content_type
    assert "Project: RummerLab Scholar" in response.get_data(as_text=True)
    assert "https://github.com/RummerLab/scholar/graphs/contributors" in response.get_data(
        as_text=True
    )
    head = client.head("/humans.txt")
    assert head.status_code == 200
    assert head.data == b""
    assert head.content_length == len(response.data)


def test_scholar_listing(client):
    assert client.get("/scholars").json == {"scholars": sorted(s[0] for s in SCHOLARS)}


@pytest.mark.parametrize("scholar_id,name,family", SCHOLARS)
def test_saved_author_serves_website_profile_and_publications(client, scholar_id, name, family):
    author = author_data(scholar_id, name, family)
    save_author_by_id(author, scholar_id, serve.SCHOLAR_DATA_DIR_ABS)
    full = client.get(f"/scholar/{scholar_id}")
    assert full.status_code == 200
    assert full.json == author
    assert full.json["schema_version"] == 1
    assert full.json["last_fetched"]

    profile = client.get(f"/scholar/{scholar_id}/gscholar")
    assert profile.status_code == 200
    assert profile.json == {key: value for key, value in author.items() if key != "publications"}
    assert client.get(f"/scholar/{scholar_id}/gscholar?include_publications=1").json == author

    page = client.get(f"/scholar/{scholar_id}/publications?limit=2&offset=1")
    assert page.status_code == 200
    assert page.json == {
        "id": scholar_id,
        "total": 3,
        "limit": 2,
        "offset": 1,
        "publications": author["publications"][1:3],
    }


@pytest.mark.parametrize("selector", ["parts", "fields"])
@pytest.mark.parametrize(
    "parts", ["profile", "publications", "pubs", "profile,publications", "all"]
)
def test_partial_scholar_responses(client, selector, parts):
    author = author_data(*SCHOLARS[0])
    response = client.get(f"/scholar/{SCHOLARS[0][0]}?{selector}={parts}")
    assert response.status_code == 200
    body = response.json
    if parts == "all":
        assert body == author
    else:
        assert body["id"] == author["scholar_id"]
        if "profile" in parts:
            assert body["profile"]["citedby"] == 123
            assert ("publications" in body["profile"]) == ("," in parts)
        else:
            assert "profile" not in body
        if parts != "profile":
            assert body["publications"] == author["publications"]
        else:
            assert "publications" not in body["profile"]
            assert "publications" not in body


@pytest.mark.parametrize("query", ["limit=no", "offset=no", "limit=-1", "offset=-1"])
def test_invalid_pagination_returns_json_error(client, query):
    response = client.get(f"/scholar/{SCHOLARS[0][0]}/publications?{query}")
    assert response.status_code == 400
    assert "Invalid pagination" in response.json["error"]


@pytest.mark.parametrize(
    "query,limit,offset",
    [("", 50, 0), ("limit=999", 200, 0), ("limit=0", 0, 0), ("offset=999", 50, 999)],
)
def test_pagination_boundaries(client, query, limit, offset):
    response = client.get(f"/scholar/{SCHOLARS[0][0]}/publications?{query}")
    assert response.status_code == 200
    assert response.json["limit"] == limit
    assert response.json["offset"] == offset
    assert response.json["total"] == 3
    expected = author_data(*SCHOLARS[0])["publications"][offset : offset + limit]
    assert response.json["publications"] == expected


@pytest.mark.parametrize("suffix", ["", "/gscholar", "/publications"])
@pytest.mark.parametrize(
    "scholar_id,status", [("bad", 400), ("abcdefghijkl", 404), ("...........x", 400)]
)
def test_invalid_or_missing_scholars(client, suffix, scholar_id, status):
    response = client.get(f"/scholar/{scholar_id}{suffix}")
    assert response.status_code == status
    assert "error" in response.json


@pytest.mark.parametrize("suffix", ["", "/gscholar", "/publications"])
@pytest.mark.parametrize("contents", ["{broken", "[]", "null", '"text"', "42"])
def test_corrupt_author_file_returns_json_error(client, suffix, contents):
    path = serve._scholar_data_path_for_id(SCHOLARS[0][0])
    path.write_text(contents, encoding="utf-8")
    response = client.get(f"/scholar/{SCHOLARS[0][0]}{suffix}")
    assert response.status_code == 500
    assert response.json == {"error": "Invalid scholar data"}


def test_invalid_parts(client):
    response = client.get(f"/scholar/{SCHOLARS[0][0]}?parts=secret")
    assert response.status_code == 400
    assert "error" in response.json


def test_missing_data_directory_lists_no_scholars(client, monkeypatch, tmp_path):
    monkeypatch.setattr(serve, "SCHOLAR_DATA_DIR_ABS", str(tmp_path / "missing"))
    assert client.get("/scholars").json == {"scholars": []}


METRIC_ROUTES = ("altmetric", "google-citations", "crossref")


@pytest.mark.parametrize("scholar_id,name,family", SCHOLARS)
@pytest.mark.parametrize("route", METRIC_ROUTES)
@pytest.mark.parametrize("encoded", [False, True])
def test_cached_metrics_website_contract(client, scholar_id, name, family, route, encoded):
    doi = author_data(scholar_id, name, family)["publications"][0]["doi"]
    response = client.get(f"/{route}/{quote(doi, safe='') if encoded else doi}")
    assert response.status_code == 200
    body = response.json
    if route == "crossref":
        assert body["message"]["DOI"] == doi
        assert body["message"]["is-referenced-by-count"] == 23
    else:
        assert body["doi"] == doi
        assert body["score" if route == "altmetric" else "citations"] == (
            7 if route == "altmetric" else 42
        )
    assert body["last_fetch"]
    assert body["last_successful_fetch"]
    assert body["last_fetched_result"] == "success"
    assert response.headers["Cache-Control"] == "public, max-age=86400, must-revalidate"
    assert response.headers["Last-Modified"]
    etag = response.headers["ETag"]
    cached = client.get(f"/{route}/{doi}", headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.data == b""
    assert cached.headers["ETag"] == etag
    assert (
        client.get(f"/{route}/{doi}", headers={"If-None-Match": '"different"'}).status_code == 200
    )


@pytest.mark.parametrize("route", METRIC_ROUTES)
@pytest.mark.parametrize(
    "doi", ["invalid", "10.1234/has space", "10.1234/%ZZ", "10.1234/../../secret"]
)
def test_invalid_doi_rejected_before_fetch(client, monkeypatch, route, doi):
    fetcher = {
        "altmetric": "fetch_altmetric_score",
        "google-citations": "fetch_google_scholar_citations",
        "crossref": "fetch_crossref_for_api",
    }[route]
    fetch = Mock(side_effect=AssertionError("Invalid DOI reached provider"))
    monkeypatch.setattr(serve, fetcher, fetch)
    response = client.get(f"/{route}/{doi}")
    assert response.status_code == 400
    assert response.json["error"].startswith("Invalid DOI")
    assert response.headers["Cache-Control"] == "no-store"
    fetch.assert_not_called()


@pytest.mark.parametrize("family", ["Rummer", "Wu", "Bergseth", "Unrelated"])
def test_author_validation_through_metric_routes(client, monkeypatch, family):
    doi = "10.1234/uncached"
    crossref = crossref_response_from_message(
        {"title": ["Synthetic paper"], "author": [{"given": "Test", "family": family}]}
    )
    monkeypatch.setattr(metrics, "get_crossref_metadata_cached", lambda *args, **kwargs: crossref)
    altmetric = Mock(
        return_value=SimpleNamespace(
            ok=True,
            url=f"https://www.altmetric.com/details/doi/{doi}",
            text='<div class="document-header"><h1>Synthetic paper</h1></div>'
            '<div class="score-panel"><img src="https://badges.altmetric.com/?score=7"></div>',
        )
    )
    scholar = Mock(
        return_value=SimpleNamespace(
            ok=True,
            url="https://scholar.google.com/scholar",
            text='<div class="gs_r"><a href="/scholar?cites=123">Cited by 42</a></div>',
        )
    )
    monkeypatch.setattr("src.altmetric._get_with_proxy_retries", altmetric)
    monkeypatch.setattr(metrics._scholar_session, "get", scholar)
    monkeypatch.setattr(metrics, "get_request_proxy_chain", lambda: [None])
    monkeypatch.setattr(metrics, "get_request_proxy_chain_summary", lambda: "test transport")
    for route, key, expected in [("altmetric", "score", 7), ("google-citations", "citations", 42)]:
        response = client.get(f"/{route}/{doi}")
        if family == "Unrelated":
            assert response.status_code == 401
            assert response.json["last_fetched_result"] == "author_not_allowed"
            assert response.headers["Cache-Control"] == "no-store"
        else:
            assert response.status_code == 200
            assert response.json[key] == expected
    assert altmetric.call_count == scholar.call_count == (0 if family == "Unrelated" else 1)


@pytest.mark.parametrize(
    "route,fetcher,result,status",
    [
        (
            "altmetric",
            "fetch_altmetric_score",
            metrics.AltmetricResult(doi="10.1234/fail", score=None, found=False),
            401,
        ),
        (
            "google-citations",
            "fetch_google_scholar_citations",
            metrics.ScholarCitationsResult(doi="10.1234/fail", citations=None, found=False),
            401,
        ),
        (
            "crossref",
            "fetch_crossref_for_api",
            metrics.CrossrefResult(doi="10.1234/fail", found=False),
            404,
        ),
    ],
)
def test_metric_errors_are_not_cached(client, monkeypatch, route, fetcher, result, status):
    result = replace(
        result,
        error_reason="Upstream unavailable",
        last_fetch="2026-01-01T00:00:00Z",
        last_successful_fetch="2025-12-31T00:00:00Z",
        last_fetched_result="error",
    )
    fetch = Mock(return_value=result)
    monkeypatch.setattr(serve, fetcher, fetch)
    response = client.get(f"/{route}/10.1234/fail?refresh=1", headers={"If-None-Match": "*"})
    assert response.status_code == status
    assert response.json["reason"] == "Upstream unavailable"
    assert response.json["last_successful_fetch"] == result.last_successful_fetch
    assert response.headers["Cache-Control"] == "no-store"
    assert "ETag" not in response.headers
    fetch.assert_called_once_with("10.1234/fail", force_refresh=True)


@pytest.mark.parametrize("citations", [0, None])
def test_zero_or_unavailable_citations_remain_distinct(client, monkeypatch, citations):
    result = metrics.ScholarCitationsResult(
        doi="10.1234/zero",
        citations=citations,
        found=True,
        last_fetch="2026-01-01T00:00:00Z",
        warning="Scholar blocked" if citations is None else None,
    )
    monkeypatch.setattr(serve, "fetch_google_scholar_citations", lambda *args, **kwargs: result)
    response = client.get("/google-citations/10.1234/zero")
    assert response.status_code == 200
    assert response.json["citations"] == citations
    if citations is None:
        assert response.json["warning"] == "Scholar blocked"
