"""Exercise the reusable HTTP smoke assertions against a real local socket."""

from threading import Thread

from werkzeug.serving import make_server

from src import scholar_citations, serve
from tests.api_support import seed_api_data
from tests.smoke_web import check_http_contract


def test_running_api_http_contract(monkeypatch, tmp_path):
    seed_api_data(tmp_path)
    monkeypatch.setattr(serve, "SCHOLAR_DATA_DIR_ABS", str(tmp_path / "scholar_data"))
    monkeypatch.setattr(scholar_citations, "CACHE_DIR", str(tmp_path / "doi_metrics"))
    server = make_server("127.0.0.1", 0, serve.app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        check_http_contract(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
