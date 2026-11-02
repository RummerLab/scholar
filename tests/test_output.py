"""Unit tests for output module."""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from src.output import (
    SCHEMA_VERSION,
    author_json_path,
    get_last_successful_indices,
    is_fresh,
    load_author,
    load_author_by_id,
    save_author,
    save_author_by_id,
    set_last_successful_index,
)


def test_load_author_missing_file():
    assert load_author("/nonexistent/path.json") is None


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "author.json")
        author = {"name": "Test Author", "publications": []}
        save_author(author, path)
        loaded = load_author(path)
        assert loaded is not None
        assert loaded["name"] == "Test Author"
        assert loaded["schema_version"] == SCHEMA_VERSION
        assert "last_fetched" in loaded


def test_save_and_load_by_id_roundtrip():
    from src.output import load_author_by_id, save_author_by_id

    with tempfile.TemporaryDirectory() as d:
        author = {"name": "Test Author", "publications": []}
        path = save_author_by_id(author, "ynWS968AAAAJ", d)
        assert path.name == "ynWS968AAAAJ.json"
        loaded = load_author_by_id("ynWS968AAAAJ", d)
        assert loaded is not None
        assert loaded["name"] == "Test Author"


def test_save_author_by_id_rejects_traversal():
    from src.output import save_author_by_id

    with tempfile.TemporaryDirectory() as d:
        try:
            save_author_by_id({"name": "X"}, "../etc/passwd", d)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_save_author_can_skip_last_fetched_bump():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "author.json")
        old = "2020-01-01T00:00:00"
        author = {"name": "X", "publications": [], "last_fetched": old}
        save_author(author, path, bump_last_fetched=False)
        loaded = load_author(path)
        assert loaded is not None
        assert loaded["last_fetched"] == old


def test_is_fresh_none():
    assert is_fresh(None, 3600) is False


def test_is_fresh_empty():
    assert is_fresh("", 3600) is False


def test_is_fresh_recent():
    now = datetime.now().isoformat()
    assert is_fresh(now, 3600) is True


def test_is_fresh_old():
    old = (datetime.now() - timedelta(days=8)).isoformat()
    assert is_fresh(old, 7 * 24 * 3600) is False


def test_get_last_successful_indices_default():
    data = {}
    indices = get_last_successful_indices(data)
    assert indices["coauthor"] == -1
    assert indices["publication"] == -1


def test_set_and_get_last_successful_index():
    author = {}
    set_last_successful_index(author, "coauthor", 5)
    set_last_successful_index(author, "publication", 10)
    assert author["_last_successful_coauthor_index"] == 5
    assert author["_last_successful_publication_index"] == 10
    indices = get_last_successful_indices(author)
    assert indices["coauthor"] == 5
    assert indices["publication"] == 10


def test_author_json_path_rejects_traversal(tmp_path):
    assert author_json_path("../etc/passwd", str(tmp_path)) is None
    assert author_json_path("..", str(tmp_path)) is None


def test_load_author_by_id_rejects_traversal(tmp_path):
    with pytest.raises(ValueError, match="Invalid scholar_id"):
        load_author_by_id("../x", str(tmp_path))


def test_save_and_load_author_by_id_roundtrip(tmp_path):
    author = {"name": "Test Author", "publications": []}
    path = save_author_by_id(author, "ynWS968AAAAJ", str(tmp_path))
    assert path.name == "ynWS968AAAAJ.json"
    loaded = load_author_by_id("ynWS968AAAAJ", str(tmp_path))
    assert loaded is not None
    assert loaded["name"] == "Test Author"
    assert loaded["schema_version"] == SCHEMA_VERSION
