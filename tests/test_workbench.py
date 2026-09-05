"""Local workbench contracts: source fidelity, downloads, and HTTP boundaries."""

import http.client
import io
import json
import threading
from importlib import resources
from zipfile import ZipFile

import pytest

from recourse import workbench
from recourse.casefile import build_casefile
from recourse.cli import main
from recourse.filings import render_all


STORY = "On 2026-03-01 I wired $1,000.\n\nOn 2026-03-02 I wired $2,500 more."
FILES = {"casefile.json", "ic3_draft.md", "freeze_letter.md", "action_plan.md", "unverified.md"}


@pytest.fixture
def server():
    httpd = workbench.make_server(port=0)
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def request(server, method="GET", path="/", body=None, headers=None):
    host, port = server.server_address
    merged = {"Host": f"{host}:{port}"}
    if method == "POST":
        merged.update({"Origin": f"http://{host}:{port}",
                       "Content-Type": "application/json", "X-Recourse-Request": "1"})
    merged.update(headers or {})
    conn = http.client.HTTPConnection(host, port, timeout=3)
    try:
        conn.request(method, path, body=body, headers=merged)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def test_review_reuses_canonical_engine_and_carries_exact_source_quotes():
    review = workbench.build_review(STORY)
    case = build_casefile(STORY)
    assert review["case"] == case.to_dict()
    assert review["documents"] == render_all(case)
    assert review["audit"] == {"passed": True, "violations": []}
    assert all(e["verbatim"] in STORY for e in review["case"]["evidence"])
    assert review == workbench.build_review(STORY)


def test_bundle_contains_exactly_five_cli_equivalent_files_and_is_deterministic():
    review = workbench.build_review(STORY)
    first = workbench.build_bundle(review)
    assert first == workbench.build_bundle(workbench.build_review(STORY))
    with ZipFile(io.BytesIO(first)) as archive:
        assert set(archive.namelist()) == FILES
        assert json.loads(archive.read("casefile.json")) == review["case"]
        for name, document in review["documents"].items():
            assert archive.read(name).decode() == document + "\n"
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_empty_extraction_is_reviewable_and_does_not_claim_verification():
    review = workbench.build_review("Someone tricked me online.")
    assert review["case"]["evidence"] == []
    assert review["case"]["transactions"] == []
    assert review["audit"]["passed"]
    assert any("independently verified" in note for note in review["case"]["unverified_notes"])
    assert "[NOT PROVIDED]" in review["documents"]["ic3_draft.md"]


def test_home_and_assets_are_packaged_and_have_security_headers(server):
    for path, media in (("/", "text/html"), ("/assets/workbench.css", "text/css"),
                        ("/assets/workbench.js", "text/javascript")):
        status, headers, body = request(server, path=path)
        assert status == 200
        assert headers["Content-Type"].startswith(media)
        assert body
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert "'unsafe-inline'" not in headers["Content-Security-Policy"]
        assert "Access-Control-Allow-Origin" not in headers
    assert b'lang="en"' in request(server)[2]


def test_head_does_not_send_a_body(server):
    status, headers, body = request(server, method="HEAD")
    assert status == 200
    assert int(headers["Content-Length"]) > 0
    assert body == b""


def test_example_is_the_existing_fictional_example(server):
    status, _, body = request(server, path="/api/example")
    assert status == 200
    example = resources.files("recourse").joinpath("data", "example_story.txt").read_text()
    assert json.loads(body) == {"story": example, "fictional": True}


def test_http_review_and_download_are_rebuilt_from_story(server):
    body = json.dumps({"story": STORY})
    status, _, data = request(server, "POST", "/api/case", body)
    assert status == 200
    assert json.loads(data) == workbench.build_review(STORY)
    status, headers, zipped = request(server, "POST", "/api/bundle", body)
    assert status == 200
    assert headers["Content-Type"] == "application/zip"
    assert headers["Content-Disposition"].startswith('attachment; filename="recourse-')
    assert zipped == workbench.build_bundle(workbench.build_review(STORY))


def test_successive_requests_do_not_share_case_state(server):
    for story in (STORY, "Someone tricked me online.", STORY):
        status, _, data = request(server, "POST", "/api/case", json.dumps({"story": story}))
        assert status == 200
        assert json.loads(data) == workbench.build_review(story)


def test_the_largest_allowed_ascii_story_can_be_reviewed(server):
    story = "No details. " + "a" * (workbench.MAX_STORY_CHARS - 12)
    assert len(story) == workbench.MAX_STORY_CHARS
    status, _, data = request(server, "POST", "/api/case", json.dumps({"story": story}))
    assert status == 200
    assert json.loads(data)["case"]["narrative"] == story


def test_literal_markup_stays_in_json_and_never_enters_the_html(server):
    story = '<script>alert("private")</script> <img src=x onerror=alert(1)>'
    status, _, body = request(server, "POST", "/api/case", json.dumps({"story": story}))
    assert status == 200
    assert json.loads(body)["case"]["narrative"] == story
    assert story.encode() not in request(server)[2]


@pytest.mark.parametrize("path", ["/README.md", "/src/recourse/cli.py", "/../pyproject.toml",
                                  "/assets/../cli.py", "/assets/%2e%2e/cli.py", "/?story=secret",
                                  "/api/case?story=secret", "/api/bundle", "//"])
def test_no_filesystem_or_query_routes(server, path):
    assert request(server, path=path)[0] == 404


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example"}, {"Host": "127.0.0.1.evil.example"},
    {"Host": "localhost:1"}, {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Origin": ""}, {"X-Recourse-Request": ""}, {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_cross_origin_and_rebinding_requests_are_rejected(server, headers):
    status, _, _ = request(server, "POST", "/api/case", json.dumps({"story": STORY}), headers)
    assert status == 403


def test_localhost_alias_is_allowed_only_with_matching_origin(server):
    port = server.server_port
    headers = {"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"}
    assert request(server, "POST", "/api/case", json.dumps({"story": STORY}), headers)[0] == 200
    headers["Origin"] = f"http://127.0.0.1:{port}"
    assert request(server, "POST", "/api/case", json.dumps({"story": STORY}), headers)[0] == 403


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_default_http_port_accepts_normalized_authority(server, monkeypatch, host):
    # Exercise default-port HTTP semantics without binding a privileged port.
    monkeypatch.setattr(server, "server_port", 80)
    headers = {"Host": host, "Origin": f"http://{host}"}
    assert request(server, headers=headers)[0] == 200
    assert request(server, "POST", "/api/case", json.dumps({"story": STORY}), headers)[0] == 200
    headers["Origin"] = "http://unrelated.example"
    assert request(server, "POST", "/api/case", json.dumps({"story": STORY}), headers)[0] == 403


@pytest.mark.parametrize("payload", [{}, {"story": " "}, {"story": None}, {"story": 23},
                                      [], {"story": STORY, "case": {"invented": True}},
                                      {"story": "x" * (workbench.MAX_STORY_CHARS + 1)}])
def test_invalid_story_contracts_are_rejected(server, payload):
    assert request(server, "POST", "/api/case", json.dumps(payload))[0] == 400


@pytest.mark.parametrize("body", [b"{bad json}", b'\xff', b'"' + b"x" * 10,
                                   b'{"story":"one","story":"two"}', b'{"story":"\\ud800"}'])
def test_malformed_body_is_rejected(server, body):
    assert request(server, "POST", "/api/case", body)[0] == 400


def test_body_limit_is_enforced_before_reading_or_building(server, monkeypatch):
    monkeypatch.setattr(workbench, "build_review", lambda _: pytest.fail("must not build"))
    # The client sends no huge payload; the server must reject from its header alone.
    status, _, _ = request(server, "POST", "/api/case", b"{}",
                           {"Content-Length": str(workbench.MAX_REQUEST_BYTES + 1)})
    assert status == 413


@pytest.mark.parametrize("headers,code", [
    ({"Content-Type": "text/plain"}, 415), ({"Content-Encoding": "gzip"}, 415),
    ({"Content-Length": "-1"}, 400), ({"Content-Length": "bad"}, 400),
    ({"Transfer-Encoding": "chunked"}, 400),
])
def test_unsupported_body_framing_is_rejected(server, headers, code):
    assert request(server, "POST", "/api/case", b"{}", headers)[0] == code


def test_audit_failure_is_visible_and_blocks_bundle(server, monkeypatch):
    monkeypatch.setattr(workbench, "verify_no_invention", lambda *_: ["test unsupported fact"])
    body = json.dumps({"story": STORY})
    status, _, data = request(server, "POST", "/api/case", body)
    assert status == 200
    assert json.loads(data)["audit"] == {"passed": False, "violations": ["test unsupported fact"]}
    status, _, data = request(server, "POST", "/api/bundle", body)
    assert status == 422
    assert "error" in json.loads(data)
    with pytest.raises(ValueError, match="audit"):
        workbench.build_bundle(workbench.build_review(STORY))


def test_unexpected_errors_do_not_expose_source_or_exception(server, monkeypatch, capsys):
    def fail(_):
        raise RuntimeError("sensitive marker")
    monkeypatch.setattr(workbench, "build_review", fail)
    status, _, body = request(server, "POST", "/api/case", json.dumps({"story": "private story"}))
    assert status == 500
    assert b"sensitive marker" not in body and b"private story" not in body
    request(server, path="/private-story-marker")
    assert capsys.readouterr() == ("", "")


def test_only_loopback_is_bound(server):
    assert server.server_address[0] == "127.0.0.1"


def test_a_running_workbench_owns_its_port(server):
    with pytest.raises(OSError):
        workbench.make_server(port=server.server_port)


def test_missing_and_duplicate_framing_headers_are_rejected(server):
    host, port = server.server_address
    for extra_headers, code in (([], 411), ([("Content-Length", "2"), ("Content-Length", "2")], 400)):
        conn = http.client.HTTPConnection(host, port, timeout=3)
        try:
            conn.putrequest("POST", "/api/case")
            conn.putheader("Origin", f"http://{host}:{port}")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("X-Recourse-Request", "1")
            for key, value in extra_headers:
                conn.putheader(key, value)
            conn.endheaders()
            response = conn.getresponse()
            assert response.status == code
            response.read()
        finally:
            conn.close()


def test_get_cannot_build_and_unknown_write_routes_do_not_process_data(server, monkeypatch):
    monkeypatch.setattr(workbench, "build_review", lambda _: pytest.fail("must not build"))
    assert request(server, path="/api/case")[0] == 404
    assert request(server, "POST", "/api/case/", b"{}")[0] == 404
    assert request(server, "POST", "/api/case?story=secret", b"{}")[0] == 404
    assert request(server, "PUT", "/api/case", b"{}")[0] == 501


def test_cli_serve_is_lazy_and_passes_the_port(monkeypatch):
    called = []
    monkeypatch.setattr(workbench, "serve", lambda port: called.append(port) or 0)
    assert main(["serve", "--port", "9876"]) == 0
    assert called == [9876]


@pytest.mark.parametrize("port", ["-1", "65536", "bad"])
def test_cli_rejects_invalid_port(port):
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--port", port])
    assert exc.value.code == 2
