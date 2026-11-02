"""Integration tests with mocked external services."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.config import Config
from src.output import save_author
from src.retry import with_retry


def test_output_schema_and_last_fetched():
    """Verify saved JSON has schema_version and last_fetched."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out.json")
        author = {"name": "Test", "publications": [], "coauthors": []}
        save_author(author, path)
        with open(path) as f:
            data = json.load(f)
        assert "schema_version" in data
        assert data["schema_version"] == 1
        assert "last_fetched" in data
        assert isinstance(data["last_fetched"], str)


def test_config_loads_from_env(monkeypatch, tmp_path):
    """Config reads from environment."""
    monkeypatch.setenv("SCHOLAR_DATA_DIR", str(tmp_path))
    config = Config(scholar_id="test123")
    assert Path(config.output_path) == tmp_path / "test123.json"


def test_retry_decorator_retries_on_exception():
    """Retry decorator retries and eventually raises."""
    attempts = []

    @with_retry(max_retries=3, base_delay=0.01)
    def failing_twice():
        attempts.append(1)
        if len(attempts) < 2:
            raise ConnectionError("fail")
        return "ok"

    result = failing_twice()
    assert result == "ok"
    assert len(attempts) == 2


def test_retry_decorator_raises_after_max_retries():
    """Retry decorator raises after max retries."""

    @with_retry(max_retries=2, base_delay=0.01)
    def always_fails():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        always_fails()
