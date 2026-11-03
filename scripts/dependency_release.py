"""Resolve aged dependencies and release only when the production graph changes."""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PIN = re.compile(r"^([\w.-]+)==([^\s;]+)(?:\s*;\s*(.*?))?\s*(?:\\)?$")
VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)$")


def lock_graph(content: str) -> frozenset:
    """Compare versions and environment markers, ignoring hashes and comments."""
    graph = set()
    for line in content.splitlines():
        match = PIN.fullmatch(line.rstrip(" \\"))
        if match:
            name, version, marker = match.groups()
            graph.add((re.sub(r"[-_.]+", "-", name).lower(), version, marker or ""))
    return frozenset(graph)


def upgrade_constraints(content: str) -> str:
    """Keep upgrades in the current major (minor for 0.x), without downgrades."""
    lines = []
    for name, version, marker in sorted(lock_graph(content)):
        public = version.split("+")[0]
        numbers = public.split(".")
        major = int(numbers[0])
        minor = int(numbers[1]) if len(numbers) > 1 else 0
        ceiling = str(major + 1) if major else f"0.{minor + 1}"
        suffix = f" ; {marker}" if marker else ""
        lines.append(f"{name}>={public},<{ceiling}{suffix}")
    return "\n".join(lines) + "\n"


def frontend_graph(projects: list) -> frozenset:
    """Include production transitives; omit dev and untracked node_modules."""
    graph = set()

    def visit(node):
        for group in ("dependencies", "optionalDependencies"):
            for name, dependency in node.get(group, {}).items():
                graph.add((name, dependency["version"]))
                visit(dependency)

    for project in projects:
        visit(project)
    return frozenset(graph)


def next_version(project_version: str, tags: list[str]) -> str:
    """Use existing stable tags as the authority when project metadata lags."""
    versions = [tuple(map(int, VERSION.fullmatch(project_version).groups()))]
    versions.extend(
        tuple(map(int, match.groups())) for tag in tags if (match := VERSION.fullmatch(tag))
    )
    major, minor, patch = max(versions)
    return f"{major}.{minor}.{patch + 1}"


def run(*command, cwd=None, capture=False):
    """Run a tool and fail the workflow if resolution or validation fails."""
    executable = shutil.which(command[0])
    if executable is None:
        raise RuntimeError(f"Required tool is missing: {command[0]}")
    return subprocess.run(
        [executable, *command[1:]],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    ).stdout


def output(**values):
    """Expose decisions to Actions, while remaining usable locally."""
    lines = "".join(
        f"{key}={str(value).lower() if isinstance(value, bool) else value}\n"
        for key, value in values.items()
    )
    print(lines, end="")
    if path := os.environ.get("GITHUB_OUTPUT"):
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(lines)


def update(root: Path) -> None:
    """Upgrade bounded Python/npm graphs using the three-day quarantine."""
    policy = json.loads((root / ".github/dependency-policy.json").read_text())
    files = ["requirements.lock", "requirements-dev.lock"]
    frontend = root / "webapp/frontend"
    if frontend.is_dir():
        files += ["webapp/frontend/package.json", "webapp/frontend/pnpm-lock.yaml"]
    before = {name: (root / name).read_text() for name in files}
    old_frontend = frozenset()
    if frontend.is_dir():
        old_frontend = frontend_graph(
            json.loads(
                run(
                    "pnpm",
                    "list",
                    "--prod",
                    "--lockfile-only",
                    "--depth",
                    "Infinity",
                    "--json",
                    cwd=frontend,
                    capture=True,
                )
            )
        )

    # A single cutoff applies to all packages selected in this run, including
    # build dependencies resolved by uv; production installs consume the locks.
    with tempfile.TemporaryDirectory(prefix="dependency-constraints-") as temp:
        for name, source in (
            ("requirements.lock", "requirements.txt"),
            ("requirements-dev.lock", "requirements-dev.txt"),
        ):
            constraints = Path(temp) / name
            constraints.write_text(upgrade_constraints(before[name]))
            command = [
                "uv",
                "pip",
                "compile",
                source,
                "--output-file",
                name,
                "--universal",
                "--python-version",
                policy["python"],
                "--exclude-newer",
                "3d",
                "--prerelease",
                "disallow",
                "--generate-hashes",
                "--no-header",
                "--no-annotate",
                "--upgrade",
                "--constraint",
                str(constraints),
                "--quiet",
            ]
            if name == "requirements-dev.lock":
                command += ["--constraint", "requirements.lock"]
            if policy.get("torch_backend"):
                command += ["--torch-backend", policy["torch_backend"]]
            run(*command, cwd=root)

    new_frontend = old_frontend
    if frontend.is_dir():
        run("pnpm", "update", "--depth", "Infinity", "--ignore-scripts", cwd=frontend)
        new_frontend = frontend_graph(
            json.loads(
                run(
                    "pnpm",
                    "list",
                    "--prod",
                    "--lockfile-only",
                    "--depth",
                    "Infinity",
                    "--json",
                    cwd=frontend,
                    capture=True,
                )
            )
        )
    changed = any((root / name).read_text() != content for name, content in before.items())
    release = (
        lock_graph(before["requirements.lock"])
        != lock_graph((root / "requirements.lock").read_text())
        or new_frontend != old_frontend
    )
    output(changed=changed, release=release)


def bump(root: Path) -> None:
    """Bump project metadata after tests; leave tag creation to the workflow."""
    path = root / "pyproject.toml"
    content = path.read_text()
    match = re.search(r'^version = "([^"]+)"$', content, re.MULTILINE)
    if match is None:
        raise ValueError("pyproject.toml has no project version")
    tags = run("git", "tag", "--list", "v*", cwd=root, capture=True).splitlines()
    version = next_version(match[1], tags)
    content = content[: match.start(1)] + version + content[match.end(1) :]
    path.write_text(content)
    output(version=version)


def main() -> None:
    """Run from the repository root: update, or bump after validation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("update", "bump"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    (update if args.command == "update" else bump)(root)


if __name__ == "__main__":
    main()
