"""A stateless, loopback-only browser surface for the deterministic engine.

Only packaged UI assets are served. Stories live in request memory and are
rebuilt for each operation; there is no case store, request log, model call,
or outbound network client. This is a local tool, not a public web service.
"""

from __future__ import annotations

import io
import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .audit import verify_no_invention
from .casefile import build_casefile
from .filings import render_all

MAX_REQUEST_BYTES = 64 * 1024
MAX_STORY_CHARS = 16_000
DEFAULT_PORT = 8765
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
    "/assets/workbench.js": ("workbench.js", "text/javascript; charset=utf-8"),
}
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


def build_review(story: str) -> dict[str, Any]:
    """Build the existing CaseFile and drafts, then run the existing audit.

    A failed audit remains inspectable, but the HTTP download is blocked.
    Passing means source consistency under the audit's supported patterns;
    it does not establish that reported events or counterparties are real.
    """
    case = build_casefile(story)
    documents = render_all(case)
    violations = verify_no_invention(story, case, documents)
    return {
        "case": case.to_dict(),
        "documents": documents,
        "audit": {"passed": not violations, "violations": violations},
    }


def build_bundle(review: dict[str, Any]) -> bytes:
    """Package a freshly audited review using fixed metadata, in memory.

    Each file matches the offline CLI's contents, including its final newline.
    This accepts an internal review, never caller-supplied case data over HTTP.
    """
    if not review["audit"]["passed"]:
        raise ValueError("The source audit must pass before download.")
    contents = {
        "casefile.json": json.dumps(review["case"], indent=2, ensure_ascii=False),
        **review["documents"],
    }
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for filename, content in contents.items():
            entry = ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o600 << 16
            archive.writestr(entry, (content + "\n").encode("utf-8"))
    return buffer.getvalue()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class _LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_port = False

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address

    def handle_error(self, request, client_address):
        # The default handler prints a traceback. Do not expose request data
        # or source-containing exception messages through terminal logs.
        pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "Recourse"
    sys_version = ""

    def log_message(self, format, *args):
        # Neither URLs nor bodies belong in a log for this local workbench.
        pass

    def _respond(self, status: int, body: bytes, media: str, **extra: str) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Connection", "close")
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._respond(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                      "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler can include a raw request target in its
        # default HTML error. Use a generic JSON message with no reflection.
        self._error(code, HTTPStatus(code).phrase)

    def _route(self) -> str:
        # Use the original target: BaseHTTPRequestHandler normalizes a leading
        # double slash before dispatch. Queries and encoded paths are not routes.
        return self.requestline.split()[1]

    def _trusted_request(self, *, write: bool = False) -> bool:
        port = self.server.server_port
        hosts = self.headers.get_all("Host", [])
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed_hosts.update({"127.0.0.1", "localhost"})
        if len(hosts) != 1 or hosts[0] not in allowed_hosts:
            self._error(403, "Open this workbench at its printed localhost address.")
            return False
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [f"http://{hosts[0]}"]:
            self._error(403, "Only requests from this workbench's own page are accepted.")
            return False
        if write:
            if (not origins or self.headers.get_all("X-Recourse-Request", []) != ["1"]
                    or self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"):
                self._error(403, "Build and download from this workbench's own page.")
                return False
        return True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._trusted_request():
            return
        route = self._route()
        if route in _ASSETS:
            filename, media = _ASSETS[route]
            body = resources.files("recourse").joinpath("web", filename).read_bytes()
            self._respond(200, body, media)
        elif route == "/api/example":
            story = resources.files("recourse").joinpath("data", "example_story.txt").read_text(
                encoding="utf-8")
            self._json(200, {"story": story, "fictional": True})
        else:
            self._error(404, "This route does not exist.")

    def _read_story(self) -> str | None:
        if self.headers.get("Transfer-Encoding") is not None:
            self._error(400, "Transfer encoding is not supported.")
            return None
        if (self.headers.get_content_type() != "application/json"
                or self.headers.get("Content-Encoding") is not None):
            self._error(415, "Send an uncompressed JSON request.")
            return None
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            self._error(411, "A request length is required.")
            return None
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
            self._error(400, "The request length is invalid.")
            return None
        length = int(lengths[0])
        if length > MAX_REQUEST_BYTES:
            self._error(413, "The request exceeds the 64 KiB limit. Shorten your story.")
            return None
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete body")
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if not isinstance(data, dict) or set(data) != {"story"}:
                raise ValueError("Expected only story")
            story = data["story"]
            if (not isinstance(story, str) or not story.strip()
                    or len(story) > MAX_STORY_CHARS):
                raise ValueError("Invalid story")
            # Reject unpaired surrogate escapes, which cannot be encoded as
            # UTF-8 in the response or exported case bundle.
            story.encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            self._error(400, "Provide a JSON object with one nonempty story (up to 16,000 characters).")
            return None
        except (TimeoutError, OSError):
            self._error(408, "The request was incomplete. Try again.")
            return None
        return story

    def do_POST(self):
        if not self._trusted_request(write=True):
            return
        route = self._route()
        if route not in {"/api/case", "/api/bundle"}:
            self._error(404, "This route does not exist.")
            return
        story = self._read_story()
        if story is None:
            return
        try:
            review = build_review(story)
            if route == "/api/case":
                self._json(200, review)
            elif not review["audit"]["passed"]:
                self._error(422, "The source audit found unsupported facts. Review the audit notes before downloading.")
            else:
                filename = f"recourse-{review['case']['case_id'][:12]}-drafts.zip"
                self._respond(200, build_bundle(review), "application/zip",
                              **{"Content-Disposition": f'attachment; filename="{filename}"'})
        except Exception:
            self._error(500, "This story could not be processed. Try a shorter, simpler account of what happened.")


def make_server(port: int = DEFAULT_PORT) -> _LocalServer:
    """Bind IPv4 loopback only. Port zero is useful for tests or a free port."""
    return _LocalServer(("127.0.0.1", port), _Handler)


def serve(port: int = DEFAULT_PORT) -> int:
    try:
        server = make_server(port)
    except OSError:
        print("Could not start the local workbench. Try a different --port.")
        return 2
    print(f"Recourse workbench: http://127.0.0.1:{server.server_port}", flush=True)
    print("Local processing only. Nothing is filed or sent. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
