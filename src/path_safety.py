"""Path containment helpers recognized by CodeQL ``py/path-injection``.

``pathlib.Path.resolve()`` / ``relative_to()`` are not modeled as sanitizers by
CodeQL (github/codeql#17226). ``os.path.realpath`` + ``startswith(base + sep)``
is the pattern CodeQL treats as a containment barrier.

``str.replace("..", "")`` is also applied so Snyk Code treats the value as
sanitized before it reaches ``open`` / ``Path.open``.
"""

from __future__ import annotations

import os
from pathlib import Path


def _snyk_strip_dotdot(value: str) -> str:
    """Remove ``..`` sequences; Snyk Code models ``str.replace`` as a sanitizer."""
    return value.replace("..", "")


def safe_path_under(base_dir: str | os.PathLike[str], *parts: str) -> Path | None:
    """
    Join basename ``parts`` under ``base_dir`` and return a ``Path`` only if the
    result stays inside the real path of ``base_dir``.

    Each part must be a single path segment (no separators, no ``..``).
    Returns ``None`` on traversal / escape attempts.
    """
    clean_parts: list[str] = []
    for part in parts:
        if not part or part in (".", "..") or os.path.isabs(part):
            return None
        # Reject / and \\ on all platforms (os.altsep is None on POSIX).
        if "/" in part or "\\" in part:
            return None
        # Reject (do not silently remap) any segment that contains ``..``.
        # Still call replace so Snyk Code models a sanitizer on the taint path.
        stripped = _snyk_strip_dotdot(part)
        if stripped != part or not stripped:
            return None
        # CodeQL-modeled sanitizer: basename strips any remaining dir components.
        name = os.path.basename(stripped)
        if name != stripped or not name or name in (".", ".."):
            return None
        clean_parts.append(name)

    base_raw = os.fspath(base_dir)
    base_stripped = _snyk_strip_dotdot(base_raw)
    # Configured roots must not contain ``..`` either.
    if base_stripped != base_raw:
        return None
    base = os.path.realpath(base_stripped)
    candidate = os.path.realpath(os.path.join(base, *clean_parts))
    # Roots already end with sep (`/` or `C:\`); appending again yields `//` / `C:\\`
    # and breaks startswith for every child path.
    base_prefix = base if base.endswith(os.sep) else base + os.sep
    if candidate == base or candidate.startswith(base_prefix):
        return Path(candidate)
    return None
