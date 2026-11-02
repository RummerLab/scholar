"""Build and verify the production image: python -m tests.smoke_web.

Requires Docker. Runs the image's normal entrypoint as its normal user, without
secrets or external networking. Fixtures live only in a temporary directory.
"""

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from tests.api_support import SCHOLARS, seed_api_data


def check_http_contract(base_url: str) -> None:
    """Fail if a running server breaks the website's profile/metrics contracts."""

    def get(path, headers=None):
        request = Request(base_url.rstrip("/") + path, headers=headers or {})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    status, _, body = get("/health")
    assert status == 200 and json.loads(body) == {"status": "healthy"}
    status, _, body = get("/scholars")
    assert status == 200 and json.loads(body)["scholars"] == sorted(s[0] for s in SCHOLARS)
    for scholar_id, name, family in SCHOLARS:
        status, _, body = get(f"/scholar/{scholar_id}/gscholar")
        profile = json.loads(body)
        assert status == 200 and profile["name"] == name
        assert profile["citedby"] == 123 and profile["hindex"] == 12
        assert "publications" not in profile
        status, _, body = get(f"/scholar/{scholar_id}/publications?limit=1&offset=0")
        page = json.loads(body)
        assert status == 200 and page["id"] == scholar_id and page["total"] == 3
        assert len(page["publications"]) == 1
        doi = page["publications"][0]["doi"]
        assert doi == f"10.1234/{family.lower()}-0"
        for route, key, value in [
            ("altmetric", "score", 7),
            ("google-citations", "citations", 42),
            ("crossref", "is-referenced-by-count", 23),
        ]:
            path = f"/{route}/{quote(doi, safe='')}"
            status, headers, body = get(path)
            result = json.loads(body)
            assert status == 200, (path, status, result)
            assert (result["message"] if route == "crossref" else result)[key] == value
            assert result["last_fetch"] and result["last_fetched_result"] == "success"
            assert "max-age=86400" in headers["Cache-Control"]
            status, _, body = get(path, {"If-None-Match": headers["ETag"]})
            assert status == 304 and body == b""
    status, headers, body = get("/google-citations/invalid")
    assert status == 400 and "error" in json.loads(body)
    assert headers["Cache-Control"] == "no-store"
    status, _, body = get("/scholar/abcdefghijkl/gscholar")
    assert status == 404 and "error" in json.loads(body)
    print("Production HTTP contracts passed for Rummer, Wu, and Bergseth.")


def check_image(image: str, build: bool = True) -> None:
    """Build the Dockerfiles and check their real Gunicorn server over HTTP."""
    repo = Path(__file__).resolve().parents[1]

    def docker(*args, capture=False, timeout=1200):
        return subprocess.run(
            ["docker", *args],
            cwd=repo,
            check=True,
            text=True,
            capture_output=capture,
            timeout=timeout,
        )

    if build:
        docker("build", "-f", "base/Dockerfile", "-t", "scholar-base:latest", ".")
        docker("build", "-f", "web/Dockerfile", "-t", image, ".")
    with tempfile.TemporaryDirectory(prefix="scholar-smoke-") as temp:
        root = Path(temp)
        seed_api_data(root)
        container = docker(
            "run",
            "--detach",
            "--network",
            "none",
            "--mount",
            f"type=bind,src={root / 'scholar_data'},dst=/app/scholar_data,readonly",
            "--mount",
            f"type=bind,src={root / 'doi_metrics'},dst=/app/cache/doi_metrics,readonly",
            "--mount",
            f"type=bind,src={repo / 'tests'},dst=/app/tests,readonly",
            image,
            capture=True,
        ).stdout.strip()
        try:
            deadline = time.monotonic() + 90
            while True:
                state = json.loads(docker("inspect", container, capture=True).stdout)[0]["State"]
                if not state["Running"]:
                    raise RuntimeError("Production container exited during startup")
                health = state.get("Health", {}).get("Status")
                if health == "healthy":
                    break
                if health == "unhealthy" or time.monotonic() >= deadline:
                    raise RuntimeError(f"Production container did not become healthy: {state}")
                time.sleep(1)
            docker(
                "exec",
                container,
                "python",
                "-m",
                "tests.smoke_web",
                "--url",
                "http://127.0.0.1:5000",
                timeout=120,
            )
        finally:
            try:
                docker("logs", container, timeout=30)
            finally:
                docker("rm", "--force", container, timeout=30)


def main() -> None:
    """Run container validation, or HTTP validation inside the isolated container."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="scholar-web:smoke")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--url", help="Check an already-running server seeded with API fixtures")
    args = parser.parse_args()
    if args.url:
        check_http_contract(args.url)
    else:
        check_image(args.image, build=not args.no_build)


if __name__ == "__main__":
    main()
