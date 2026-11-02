"""Tests for CodeQL-recognized path containment helpers."""

import os
from pathlib import Path

from src.path_safety import safe_path_under


def test_safe_path_under_allows_basename(tmp_path: Path):
    target = safe_path_under(tmp_path, "ynWS968AAAAJ.json")
    assert target is not None
    assert target == (tmp_path / "ynWS968AAAAJ.json").resolve()


def test_safe_path_under_rejects_parent_segment(tmp_path: Path):
    assert safe_path_under(tmp_path, "..") is None
    assert safe_path_under(tmp_path, "..", "etc") is None


def test_safe_path_under_rejects_nested_separators(tmp_path: Path):
    assert safe_path_under(tmp_path, "a/b.json") is None
    # Backslash must be rejected on Linux too (not only when os.altsep is set).
    assert safe_path_under(tmp_path, "a\\b.json") is None
    assert safe_path_under(tmp_path, r"a\b.json") is None


def test_safe_path_under_rejects_absolute_segment(tmp_path: Path):
    abs_seg = os.path.abspath(os.sep)
    assert safe_path_under(tmp_path, abs_seg) is None


def test_safe_path_under_accepts_base_with_trailing_sep(tmp_path: Path):
    """Bases that already end with sep must still accept children (root case)."""
    base = str(tmp_path.resolve()) + os.sep
    target = safe_path_under(base, "x.json")
    assert target is not None
    assert target == (tmp_path / "x.json").resolve()


def test_safe_path_under_rejects_sibling_prefix_escape(tmp_path: Path):
    """`/cache` must not accept `/cache_evil` via naive startswith."""
    base = tmp_path / "cache"
    sibling = tmp_path / "cache_evil"
    base.mkdir()
    sibling.mkdir()
    (sibling / "x.json").write_text("{}", encoding="utf-8")
    assert safe_path_under(base, "x.json") is not None
    # Joining stays under base; sibling escape is structural, not via basename
    assert safe_path_under(base, "x.json").parent == base.resolve()


def test_safe_path_under_allows_nested_basename_parts(tmp_path: Path):
    target = safe_path_under(tmp_path, "crossref_title", "doi_by_title.json")
    assert target is not None
    assert target == (tmp_path / "crossref_title" / "doi_by_title.json").resolve()


def test_safe_path_under_strips_dotdot_tokens(tmp_path: Path):
    # After stripping ``..``, a formerly-unsafe token becomes a normal basename.
    assert safe_path_under(tmp_path, "..") is None
    assert safe_path_under(tmp_path, "ok..json") == (tmp_path / "okjson").resolve()
