"""Offline release-policy tests; never resolve packages or create releases."""

from scripts.dependency_release import (
    frontend_graph,
    lock_graph,
    next_version,
    upgrade_constraints,
)


def test_hash_only_changes_do_not_release():
    before = "requests==2.34.2 \\\n    --hash=sha256:old\n"
    after = before.replace("old", "new")
    assert lock_graph(before) == lock_graph(after)


def test_transitive_and_platform_changes_release():
    before = "requests==2.34.2\nurllib3==2.8.0 ; sys_platform == 'linux'\n"
    assert lock_graph(before) != lock_graph(before.replace("2.8.0", "2.8.1"))
    assert lock_graph(before) != lock_graph(before.replace("linux", "win32"))


def test_constraints_prevent_major_bumps_and_downgrades():
    content = "Flask==3.1.3\nalpha==0.15.2\ntorch==2.11.0+cpu ; sys_platform == 'linux'\n"
    constraints = upgrade_constraints(content)
    assert "flask>=3.1.3,<4\n" in constraints
    assert "alpha>=0.15.2,<0.16\n" in constraints
    assert "torch>=2.11.0,<3 ; sys_platform == 'linux'\n" in constraints


def test_development_only_frontend_updates_do_not_release():
    project = {
        "dependencies": {"react": {"version": "18.3.1"}},
        "devDependencies": {"vite": {"version": "8.1.0"}},
    }
    before = frontend_graph([project])
    project["devDependencies"]["vite"]["version"] = "8.1.1"
    assert frontend_graph([project]) == before


def test_frontend_transitive_updates_release():
    project = {
        "dependencies": {
            "react": {"version": "18.3.1", "dependencies": {"scheduler": {"version": "0.23.0"}}}
        }
    }
    before = frontend_graph([project])
    project["dependencies"]["react"]["dependencies"]["scheduler"]["version"] = "0.23.1"
    assert frontend_graph([project]) != before


def test_version_uses_latest_stable_tag():
    assert next_version("1.2.1", ["v1.2.2", "v2.0.0-rc.1", "notes"]) == "1.2.3"
    assert next_version("0.1.0", ["v1.0.1"]) == "1.0.2"


def test_single_component_calendar_versions():
    assert (
        upgrade_constraints("openai-whisper==20250625\n") == "openai-whisper>=20250625,<20250626\n"
    )


def test_updater_enforces_age_and_production_only_release(tmp_path, monkeypatch):
    import json

    from scripts import dependency_release

    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/dependency-policy.json").write_text(json.dumps({"python": "3.12"}))
    (tmp_path / "requirements.lock").write_text("requests==2.34.2\n")
    (tmp_path / "requirements-dev.lock").write_text("requests==2.34.2\nruff==0.17.3\n")
    result = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(result))
    calls = []

    def fake_resolver(*command, cwd=None, capture=False):
        calls.append(command)
        assert command[command.index("--exclude-newer") + 1] == "3d"
        assert "--generate-hashes" in command
        assert "--prerelease" in command
        if "requirements-dev.txt" in command:
            (tmp_path / "requirements-dev.lock").write_text("requests==2.34.2\nruff==0.17.4\n")

    monkeypatch.setattr(dependency_release, "run", fake_resolver)
    dependency_release.update(tmp_path)
    assert len(calls) == 2
    assert result.read_text() == "changed=true\nrelease=false\n"


def test_runtime_transitive_update_releases(tmp_path, monkeypatch):
    import json

    from scripts import dependency_release

    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/dependency-policy.json").write_text(json.dumps({"python": "3.12"}))
    old = "requests==2.34.2\nurllib3==2.8.0\n"
    (tmp_path / "requirements.lock").write_text(old)
    (tmp_path / "requirements-dev.lock").write_text(old)
    result = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(result))

    def fake_resolver(*command, cwd=None, capture=False):
        name = command[command.index("--output-file") + 1]
        (tmp_path / name).write_text(old.replace("2.8.0", "2.8.1"))

    monkeypatch.setattr(dependency_release, "run", fake_resolver)
    dependency_release.update(tmp_path)
    assert result.read_text() == "changed=true\nrelease=true\n"
