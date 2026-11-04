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

from tests.api_support import (
    SCHOLARS,
    assert_website_metric_contract,
    assert_website_profile_contract,
    assert_website_publications_contract,
    seed_api_data,
)


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
    status, headers, body = get("/humans.txt")
    assert status == 200 and "Project: RummerLab Scholar" in body.decode("utf-8")
    assert headers.get_content_type() == "text/plain"
    status, _, body = get("/scholars")
    assert status == 200 and json.loads(body)["scholars"] == sorted(s[0] for s in SCHOLARS)
    for scholar_id, name, family in SCHOLARS:
        status, _, body = get(f"/scholar/{scholar_id}/gscholar")
        profile = json.loads(body)
        assert status == 200
        assert_website_profile_contract(profile, scholar_id, name)
        status, _, body = get(f"/scholar/{scholar_id}/publications?limit=1&offset=0")
        page = json.loads(body)
        assert status == 200
        assert_website_publications_contract(page, scholar_id, family)
        assert page["limit"] == 1 and page["offset"] == 0
        doi = page["publications"][0]["doi"]
        for route in ("altmetric", "google-citations", "crossref"):
            path = f"/{route}/{quote(doi, safe='')}"
            status, headers, body = get(path)
            result = json.loads(body)
            assert status == 200, (path, status, result)
            assert_website_metric_contract(route, result, doi, family, name)
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
