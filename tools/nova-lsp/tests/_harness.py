"""Tiny in-process LSP harness used by the smoke tests.

We don't spawn a subprocess; we just call `dispatch()` on a fresh
`ServerState` with a `BytesIO` standing in for stdout. This keeps the
tests fast (~10 ms each) and side-effect free."""
from __future__ import annotations

import io
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Make `nova_lsp` importable when running the file directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # tools/nova-lsp/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from nova_lsp.server import (  # noqa: E402  (after sys.path tweak)
    ServerState,
    dispatch,
    write_message,
    path_to_uri,
)


class LspClient:
    """Simulates an LSP client by driving `dispatch()` directly."""

    def __init__(self) -> None:
        self.state = ServerState()
        self.out = io.BytesIO()
        self._next_id = 1
        self.responses: List[Dict[str, Any]] = []
        self.notifications: List[Dict[str, Any]] = []

    # --- low-level ----------------------------------------------------------

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        before = self.out.tell()
        dispatch(self.state, msg, self.out)
        # Parse anything new the server wrote.
        self.out.seek(before)
        raw = self.out.read()
        for parsed in _parse_messages(raw):
            if "id" in parsed and ("result" in parsed or "error" in parsed):
                self.responses.append(parsed)
            else:
                self.notifications.append(parsed)
        self.out.seek(0, io.SEEK_END)

    # --- protocol helpers ---------------------------------------------------

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        rid = self._next_id
        self._next_id += 1
        self._dispatch({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        for r in reversed(self.responses):
            if r.get("id") == rid:
                return r
        raise AssertionError(f"no response for request id={rid} ({method})")

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self._dispatch({"jsonrpc": "2.0", "method": method, "params": params})

    # --- ergonomic shortcuts -----------------------------------------------

    def initialize(self, root_path: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "processId": None,
            "rootUri": path_to_uri(root_path) if root_path else None,
            "capabilities": {},
            "initializationOptions": {
                # bogus path keeps `nova --check` from running during tests
                "compilerPath": "/nonexistent/nova"
            },
        }
        if root_path:
            params["rootPath"] = root_path
        resp = self.request("initialize", params)
        self.notify("initialized", {})
        return resp

    def open(self, path: str, text: str) -> str:
        uri = path_to_uri(path)
        self.notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": "nova", "version": 1, "text": text}},
        )
        return uri


def _parse_messages(raw: bytes) -> List[Dict[str, Any]]:
    """Pull every Content-Length-framed JSON message out of `raw`."""
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(raw):
        header_end = raw.find(b"\r\n\r\n", i)
        if header_end == -1:
            break
        header = raw[i:header_end].decode("ascii", errors="replace")
        length = 0
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    length = 0
        body_start = header_end + 4
        body = raw[body_start:body_start + length]
        try:
            out.append(json.loads(body.decode("utf-8")))
        except json.JSONDecodeError:
            pass
        i = body_start + length
    return out
