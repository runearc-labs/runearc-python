"""Tiny local HTTP servers for the tests: a stand-in for failures the real API cannot be made
to produce on demand, and a receiver for real webhook deliveries."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Mapping, Optional

Responder = Callable[["Request"], None]


class Request:
    """One request as the server saw it, and the way to answer it."""

    def __init__(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        self.handler = handler
        self.method = handler.command
        self.path = handler.path
        self.headers: Mapping[str, str] = {k.lower(): v for k, v in handler.headers.items()}
        self.body = body

    def reply(
        self,
        status: int,
        body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        raw: Optional[bytes] = None,
    ) -> None:
        payload = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        self.handler.send_response(status)
        self.handler.send_header("content-type", "application/json")
        self.handler.send_header("content-length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.handler.send_header(k, v)
        self.handler.end_headers()
        self.handler.wfile.write(payload)

    def drop(self) -> None:
        self.handler.connection.close()


class Server:
    def __init__(self, responder: Responder) -> None:
        self.requests: List[Request] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                req = Request(self, self.rfile.read(length))
                outer.requests.append(req)
                responder(req)

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def error_body(code: str, message: str, fix: Optional[str] = None) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "fix": fix}}
