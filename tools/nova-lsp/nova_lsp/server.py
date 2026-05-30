"""Nova LSP server.

Implements a minimal subset of the Language Server Protocol over stdio
using only the Python standard library. Supports:

    * `initialize`, `initialized`, `shutdown`, `exit`
    * `textDocument/didOpen`, `didChange`, `didClose`
    * `textDocument/publishDiagnostics` (driven by `nova --check`)
    * `textDocument/hover` (symbol scan of `import`-ed runtime files)

Run with::

    python -m nova_lsp.server
    nova-lsp                       # if installed via pip
    nova-lsp --help                # short usage
    nova-lsp --version             # version banner
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

from nova_lsp import __version__

LOG_FILE = os.environ.get("NOVA_LSP_LOG")


def _log(msg: str) -> None:
    if not LOG_FILE:
        return
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# JSON-RPC framing (LSP base protocol).
# ---------------------------------------------------------------------------


def read_message(stream) -> Optional[Dict[str, Any]]:
    """Read one Content-Length-framed JSON-RPC message from `stream`."""
    headers: Dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None  # EOF
        line = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
        if line in ("\r\n", "\n", ""):
            break
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    raw = stream.read(length)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        _log(f"json decode error: {e}")
        return None


def write_message(stream, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    try:
        stream.write(header + data)
        stream.flush()
    except (BrokenPipeError, OSError) as e:
        _log(f"write failed: {e}")


# ---------------------------------------------------------------------------
# Document store.
# ---------------------------------------------------------------------------


@dataclass
class Document:
    uri: str
    text: str
    version: int = 0


@dataclass
class ServerState:
    documents: Dict[str, Document] = field(default_factory=dict)
    root_path: Optional[str] = None
    nova_compiler: str = "nova"
    shutdown_requested: bool = False


# ---------------------------------------------------------------------------
# Diagnostics — invoke `nova --check` on the buffer.
# ---------------------------------------------------------------------------


DIAG_LINE_RE = re.compile(
    r"^(?:(?P<file>[^\s:][^:]*):)?"
    r"(?P<line>\d+)(?::(?P<col>\d+))?:?\s*"
    r"(?P<level>error|warning|note)?:?\s*"
    r"(?P<msg>.+)$",
    re.IGNORECASE,
)


def uri_to_path(uri: str) -> Optional[str]:
    if not uri.startswith("file://"):
        return None
    parsed = urlparse(uri)
    return unquote(parsed.path)


def run_compiler_check(state: ServerState, doc: Document) -> List[Dict[str, Any]]:
    """Write `doc` to a tempfile and run `nova --check` (falling back to
    plain `nova`) over it. Parse stderr for `file:line:col: error: msg`
    style lines and emit LSP Diagnostic objects."""
    if not state.nova_compiler:
        return []
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".nova", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(doc.text)
        tmp_path = tmp.name
    try:
        for argv in (
            [state.nova_compiler, "--check", tmp_path],
            [state.nova_compiler, tmp_path, "-o", os.devnull],
        ):
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                _log(f"compiler invocation failed for {argv[0]}: {e}")
                return []
            if proc.returncode == 0:
                return []  # clean
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
            stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
            diags = parse_compiler_output(stderr + "\n" + stdout, doc)
            if diags or "--check" not in argv:
                return diags
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def parse_compiler_output(text: str, doc: Document) -> List[Dict[str, Any]]:
    diags: List[Dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = DIAG_LINE_RE.match(line)
        if not m:
            continue
        try:
            line_no = max(0, int(m.group("line")) - 1)
        except (ValueError, TypeError):
            continue
        col = m.group("col")
        try:
            col_no = max(0, int(col) - 1) if col else 0
        except ValueError:
            col_no = 0
        level = (m.group("level") or "error").lower()
        severity = {"error": 1, "warning": 2, "note": 3}.get(level, 1)
        msg = m.group("msg").strip()
        diags.append(
            {
                "range": {
                    "start": {"line": line_no, "character": col_no},
                    "end": {"line": line_no, "character": col_no + 1},
                },
                "severity": severity,
                "source": "nova",
                "message": msg,
            }
        )
    return diags


def publish_diagnostics(state: ServerState, doc: Document, out_stream) -> None:
    diagnostics = run_compiler_check(state, doc)
    write_message(
        out_stream,
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": doc.uri, "diagnostics": diagnostics},
        },
    )


# ---------------------------------------------------------------------------
# Hover — scan imported files for `fn name(args)` definitions.
# ---------------------------------------------------------------------------


IMPORT_RE = re.compile(r'^\s*import\s+"([^"]+)"')
FN_DEF_RE = re.compile(r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def collect_symbols(doc: Document, state: ServerState) -> Dict[str, str]:
    """Return `name -> "fn name(args)"` for all defs in `doc` and its imports."""
    symbols: Dict[str, str] = {}
    base_dir = os.path.dirname(uri_to_path(doc.uri) or "") or state.root_path or "."
    _scan_text(doc.text, symbols)
    for raw_line in doc.text.splitlines():
        m = IMPORT_RE.match(raw_line)
        if not m:
            continue
        rel = m.group(1)
        path = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base_dir, rel))
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                _scan_text(f.read(), symbols)
        except OSError:
            continue
    return symbols


def _scan_text(text: str, symbols: Dict[str, str]) -> None:
    for line in text.splitlines():
        m = FN_DEF_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        args = m.group(2).strip()
        sig = f"fn {name}({args})"
        symbols.setdefault(name, sig)


def word_at(text: str, line: int, character: int) -> Optional[str]:
    lines = text.splitlines()
    if not (0 <= line < len(lines)):
        return None
    src = lines[line]
    if not (0 <= character <= len(src)):
        return None
    # walk backward and forward from `character`
    start = character
    while start > 0 and (src[start - 1].isalnum() or src[start - 1] == "_"):
        start -= 1
    end = character
    while end < len(src) and (src[end].isalnum() or src[end] == "_"):
        end += 1
    if start == end:
        return None
    return src[start:end]


def handle_hover(state: ServerState, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    uri = params.get("textDocument", {}).get("uri")
    pos = params.get("position", {})
    doc = state.documents.get(uri or "")
    if not doc:
        return None
    name = word_at(doc.text, pos.get("line", 0), pos.get("character", 0))
    if not name:
        return None
    symbols = collect_symbols(doc, state)
    sig = symbols.get(name)
    if not sig:
        return None
    return {"contents": {"kind": "markdown", "value": f"```nova\n{sig}\n```"}}


# ---------------------------------------------------------------------------
# Top-level dispatcher.
# ---------------------------------------------------------------------------


def make_response(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def server_capabilities() -> Dict[str, Any]:
    return {
        "textDocumentSync": {
            "openClose": True,
            "change": 1,  # full document sync
            "save": {"includeText": True},
        },
        "hoverProvider": True,
        "diagnosticProvider": {"interFileDependencies": False, "workspaceDiagnostics": False},
    }


def dispatch(state: ServerState, msg: Dict[str, Any], out_stream) -> bool:
    """Handle one inbound message. Returns False if the server should exit."""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        root = params.get("rootPath") or params.get("rootUri")
        if root and isinstance(root, str) and root.startswith("file://"):
            root = uri_to_path(root)
        state.root_path = root if isinstance(root, str) else None
        init_opts = params.get("initializationOptions") or {}
        state.nova_compiler = init_opts.get("compilerPath") or os.environ.get(
            "NOVA_COMPILER", "nova"
        )
        write_message(
            out_stream,
            make_response(
                req_id,
                {
                    "capabilities": server_capabilities(),
                    "serverInfo": {"name": "nova-lsp", "version": __version__},
                },
            ),
        )
        return True
    if method == "initialized":
        return True
    if method == "shutdown":
        state.shutdown_requested = True
        write_message(out_stream, make_response(req_id, None))
        return True
    if method == "exit":
        return False

    if method == "textDocument/didOpen":
        td = params.get("textDocument", {})
        doc = Document(uri=td.get("uri", ""), text=td.get("text", ""), version=td.get("version", 0))
        state.documents[doc.uri] = doc
        publish_diagnostics(state, doc, out_stream)
        return True
    if method == "textDocument/didChange":
        uri = params.get("textDocument", {}).get("uri", "")
        changes = params.get("contentChanges") or []
        doc = state.documents.get(uri)
        if doc and changes:
            doc.text = changes[-1].get("text", doc.text)
            doc.version = params.get("textDocument", {}).get("version", doc.version + 1)
            publish_diagnostics(state, doc, out_stream)
        return True
    if method == "textDocument/didSave":
        uri = params.get("textDocument", {}).get("uri", "")
        doc = state.documents.get(uri)
        if doc:
            new_text = params.get("text")
            if isinstance(new_text, str):
                doc.text = new_text
            publish_diagnostics(state, doc, out_stream)
        return True
    if method == "textDocument/didClose":
        uri = params.get("textDocument", {}).get("uri", "")
        state.documents.pop(uri, None)
        # publish empty diagnostics so the client clears squigglies
        write_message(
            out_stream,
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "diagnostics": []},
            },
        )
        return True
    if method == "textDocument/hover":
        result = handle_hover(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True

    # Unknown request — respond with method-not-found if it has an id.
    if req_id is not None:
        write_message(
            out_stream,
            make_error(req_id, -32601, f"method not found: {method}"),
        )
    return True


def serve(in_stream, out_stream) -> int:
    state = ServerState()
    _log(f"nova-lsp v{__version__} started")
    while True:
        msg = read_message(in_stream)
        if msg is None:
            return 0
        try:
            keep = dispatch(state, msg, out_stream)
        except Exception as e:  # pragma: no cover — last-resort error path
            _log(f"dispatch error: {e!r}")
            req_id = msg.get("id")
            if req_id is not None:
                write_message(out_stream, make_error(req_id, -32603, f"internal error: {e}"))
            keep = True
        if not keep:
            return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nova-lsp",
        description="Nova Language Server (LSP over stdio).",
    )
    parser.add_argument(
        "--version", action="version", version=f"nova-lsp {__version__}"
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Speak LSP over stdin/stdout (default).",
    )
    parser.parse_args(argv)

    # Use raw binary streams so Content-Length byte counts line up.
    return serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
