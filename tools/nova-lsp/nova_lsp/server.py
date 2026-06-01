"""Nova LSP server.

Implements a subset of the Language Server Protocol over stdio
using only the Python standard library. Supports:

    * `initialize`, `initialized`, `shutdown`, `exit`
    * `textDocument/didOpen`, `didChange`, `didSave`, `didClose`
    * `textDocument/publishDiagnostics` (driven by `nova --check`)
    * `textDocument/hover` (symbol scan of `import`-ed runtime files)
    * `textDocument/completion` (builtins + fn/let from doc + imports)
    * `textDocument/rename` (regex-based workspace edit)
    * `textDocument/references` (regex-based occurrence scan)

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
from typing import Any, Dict, List, Optional, Set, Tuple
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
# Builtin functions — mirrored from src/compiler/codegen.nova's `is_builtin_fn`
# list. Kept in sync manually; the LSP only uses these for completion labels.
# ---------------------------------------------------------------------------


BUILTIN_FUNCTIONS: Dict[str, str] = {
    # I/O & strings
    "println":         "fn println(value)",
    "print":           "fn print(value)",
    "print_int":       "fn print_int(n)",
    "concat":          "fn concat(a, b)",
    "int_to_str":      "fn int_to_str(n)",
    "str_to_int":      "fn str_to_int(s)",
    "read_line":       "fn read_line()",
    "read_stdin":      "fn read_stdin()",
    "read_file":       "fn read_file(path)",
    "write_file":      "fn write_file(path, contents)",
    "file_size":       "fn file_size(path)",
    "mkdir":           "fn mkdir(path)",
    "unlink":          "fn unlink(path)",
    "chr":             "fn chr(code)",
    "char_at":         "fn char_at(s, i)",
    "char_code":       "fn char_code(c)",
    "substr":          "fn substr(s, start, len)",
    "starts_with":     "fn starts_with(s, prefix)",
    "ends_with":       "fn ends_with(s, suffix)",
    "str_find":        "fn str_find(s, needle)",
    "str_eq":          "fn str_eq(a, b)",
    "str_upper":       "fn str_upper(s)",
    "str_lower":       "fn str_lower(s)",
    "str_trim":        "fn str_trim(s)",
    "str_replace":     "fn str_replace(s, old, new)",
    "str_repeat":      "fn str_repeat(s, n)",
    "str_count":       "fn str_count(s, needle)",
    "strcmp":          "fn strcmp(a, b)",
    "chars":           "fn chars(s)",
    "join":            "fn join(items, sep)",
    "split":           "fn split(s, sep)",
    "pad_left":        "fn pad_left(s, n, c)",
    "pad_right":       "fn pad_right(s, n, c)",
    "hex":             "fn hex(n)",
    # Containers
    "list_new":        "fn list_new()",
    "push":            "fn push(list, value)",
    "pop":             "fn pop(list)",
    "len":             "fn len(value)",
    "list_set":        "fn list_set(list, i, v)",
    "list_copy":       "fn list_copy(list)",
    "list_slice":      "fn list_slice(list, start, end)",
    "list_remove":     "fn list_remove(list, i)",
    "append_list":     "fn append_list(dst, src)",
    "contains":        "fn contains(haystack, needle)",
    "index_of":        "fn index_of(list, value)",
    "last_index_of":   "fn last_index_of(list, value)",
    "reverse":         "fn reverse(list)",
    "sort":            "fn sort(list)",
    "map_new":         "fn map_new()",
    "map_set":         "fn map_set(m, k, v)",
    "map_get":         "fn map_get(m, k)",
    "map_has":         "fn map_has(m, k)",
    "map_remove":      "fn map_remove(m, k)",
    "map_count":       "fn map_count(m)",
    "map_merge":       "fn map_merge(a, b)",
    "keys":            "fn keys(m)",
    "values":          "fn values(m)",
    "map_list":        "fn map_list(list, fn)",
    "filter":          "fn filter(list, fn)",
    "reduce":          "fn reduce(list, fn, init)",
    "foreach":         "fn foreach(list, fn)",
    "zip":             "fn zip(a, b)",
    "flat_map":        "fn flat_map(list, fn)",
    "flatten":         "fn flatten(list)",
    "unique":          "fn unique(list)",
    "enumerate":       "fn enumerate(list)",
    "any":             "fn any(list, fn)",
    "all":             "fn all(list, fn)",
    "sum":             "fn sum(list)",
    "product":         "fn product(list)",
    "min_list":        "fn min_list(list)",
    "max_list":        "fn max_list(list)",
    "range":           "fn range(n)",
    "range_list":      "fn range_list(start, stop)",
    "range_step":      "fn range_step(start, stop, step)",
    # Math
    "abs":             "fn abs(n)",
    "min":             "fn min(a, b)",
    "max":             "fn max(a, b)",
    "float_mul":       "fn float_mul(a, b)",
    "float_div":       "fn float_div(a, b)",
    "float_add":       "fn float_add(a, b)",
    "float_sub":       "fn float_sub(a, b)",
    "float_cmp":       "fn float_cmp(a, b)",
    "float_to_str":    "fn float_to_str(f)",
    "to_float":        "fn to_float(n)",
    "from_float":      "fn from_float(f)",
    "fsqrt":           "fn fsqrt(f)",
    "to_int":          "fn to_int(value)",
    "to_str":          "fn to_str(value)",
    "int_mul":         "fn int_mul(a, b)",
    "int_add":         "fn int_add(a, b)",
    "int_sub":         "fn int_sub(a, b)",
    "int_div":         "fn int_div(a, b)",
    "int_mod":         "fn int_mod(a, b)",
    "int_shl":         "fn int_shl(a, n)",
    "int_shr":         "fn int_shr(a, n)",
    "int_and":         "fn int_and(a, b)",
    "int_or":          "fn int_or(a, b)",
    "int_xor":         "fn int_xor(a, b)",
    "random":          "fn random(max)",
    "random_seed":     "fn random_seed(seed)",
    # Runtime/system
    "exit":            "fn exit(code)",
    "assert":          "fn assert(cond)",
    "type_of":         "fn type_of(value)",
    "typename":        "fn typename(value)",
    "debug_print":     "fn debug_print(value)",
    "alloc":           "fn alloc(size)",
    "time":            "fn time()",
    "sleep_ms":        "fn sleep_ms(ms)",
    "getenv":          "fn getenv(name)",
    "get_error":       "fn get_error()",
    "__arg":           "fn __arg(i)",
    # Process/network/FFI
    "fork_process":    "fn fork_process()",
    "waitpid":         "fn waitpid(pid)",
    "exec_program":    "fn exec_program(argv)",
    "pipe_create":     "fn pipe_create()",
    "system_exec":     "fn system_exec(cmd)",
    "socket":          "fn socket(domain, type, proto)",
    "bind_socket":     "fn bind_socket(fd, addr)",
    "listen_socket":   "fn listen_socket(fd, backlog)",
    "accept_conn":     "fn accept_conn(fd)",
    "connect_socket":  "fn connect_socket(fd, addr)",
    "send_data":       "fn send_data(fd, buf)",
    "recv_data":       "fn recv_data(fd, n)",
    "close_fd":        "fn close_fd(fd)",
    "make_sockaddr_in":"fn make_sockaddr_in(host, port)",
    "ffi_open":        "fn ffi_open(libname)",
    "ffi_sym":         "fn ffi_sym(handle, name)",
    "ffi_close":       "fn ffi_close(handle)",
    "ffi_call0":       "fn ffi_call0(sym)",
    "ffi_call1":       "fn ffi_call1(sym, a1)",
    "ffi_call2":       "fn ffi_call2(sym, a1, a2)",
    "ffi_call3":       "fn ffi_call3(sym, a1, a2, a3)",
    "ffi_call4":       "fn ffi_call4(sym, a1, a2, a3, a4)",
    "ffi_call5":       "fn ffi_call5(sym, a1, a2, a3, a4, a5)",
    "ffi_calln":       "fn ffi_calln(sym, args)",
    "ffi_callf1":      "fn ffi_callf1(sym, a1)",
    "ffi_callf2":      "fn ffi_callf2(sym, a1, a2)",
    "ffi_callf3":      "fn ffi_callf3(sym, a1, a2, a3)",
    "ffi_callf4":      "fn ffi_callf4(sym, a1, a2, a3, a4)",
    # Coroutines & low-level
    "coro_new":        "fn coro_new(fn)",
    "coro_resume":     "fn coro_resume(c)",
    "coro_yield":      "fn coro_yield(value)",
    "coro_done":       "fn coro_done(c)",
    "coro_result":     "fn coro_result(c)",
    "coro_state":      "fn coro_state(c)",
    "store64":         "fn store64(addr, value)",
    "load64":          "fn load64(addr)",
    "store8":          "fn store8(addr, value)",
    "load8":           "fn load8(addr)",
    "memcpy_raw":      "fn memcpy_raw(dst, src, n)",
    "__intrinsic_dot_i32": "fn __intrinsic_dot_i32(a, b, n)",
}


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


def path_to_uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


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
# Symbol scan — used by hover, completion, rename, and references.
# ---------------------------------------------------------------------------


IMPORT_RE = re.compile(r'^\s*import\s+"([^"]+)"')
FN_DEF_RE = re.compile(r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
LET_DEF_RE = re.compile(r"^\s*let\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=(.*))?$")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Symbol:
    name: str
    signature: str  # "fn foo(a, b)" or "let X = 42"
    kind: str       # "fn" | "let" | "builtin"


def _scan_text(
    text: str,
    fn_symbols: Dict[str, Symbol],
    let_symbols: Dict[str, Symbol],
) -> None:
    for line in text.splitlines():
        m = FN_DEF_RE.match(line)
        if m:
            name = m.group(1)
            args = m.group(2).strip()
            sig = f"fn {name}({args})"
            fn_symbols.setdefault(name, Symbol(name=name, signature=sig, kind="fn"))
            continue
        m2 = LET_DEF_RE.match(line)
        if m2:
            name = m2.group(1)
            rhs = (m2.group(2) or "").strip()
            sig = f"let {name}" + (f" = {rhs}" if rhs else "")
            let_symbols.setdefault(name, Symbol(name=name, signature=sig.rstrip(), kind="let"))


def _resolved_imports(doc: Document, state: ServerState) -> List[str]:
    """Return absolute paths of files imported by `doc` (top-level only)."""
    base_dir = os.path.dirname(uri_to_path(doc.uri) or "") or state.root_path or "."
    paths: List[str] = []
    for raw_line in doc.text.splitlines():
        m = IMPORT_RE.match(raw_line)
        if not m:
            continue
        rel = m.group(1)
        path = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base_dir, rel))
        if os.path.isfile(path):
            paths.append(path)
    return paths


def collect_symbols(
    doc: Document, state: ServerState
) -> Tuple[Dict[str, Symbol], Dict[str, Symbol]]:
    """Return (fn_symbols, let_symbols) for `doc` plus its imports."""
    fns: Dict[str, Symbol] = {}
    lets: Dict[str, Symbol] = {}
    _scan_text(doc.text, fns, lets)
    seen_imports: Set[str] = set()
    pending = list(_resolved_imports(doc, state))
    while pending:
        path = pending.pop()
        if path in seen_imports:
            continue
        seen_imports.add(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        _scan_text(text, fns, lets)
        # follow transitive imports
        sub_dir = os.path.dirname(path)
        for raw_line in text.splitlines():
            m = IMPORT_RE.match(raw_line)
            if not m:
                continue
            rel = m.group(1)
            sub = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(sub_dir, rel))
            if os.path.isfile(sub) and sub not in seen_imports:
                pending.append(sub)
    return fns, lets


def word_at(text: str, line: int, character: int) -> Optional[str]:
    lines = text.splitlines()
    if not (0 <= line < len(lines)):
        return None
    src = lines[line]
    if not (0 <= character <= len(src)):
        return None
    start = character
    while start > 0 and (src[start - 1].isalnum() or src[start - 1] == "_"):
        start -= 1
    end = character
    while end < len(src) and (src[end].isalnum() or src[end] == "_"):
        end += 1
    if start == end:
        return None
    return src[start:end]


# ---------------------------------------------------------------------------
# Hover.
# ---------------------------------------------------------------------------


def handle_hover(state: ServerState, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    uri = params.get("textDocument", {}).get("uri")
    pos = params.get("position", {})
    doc = state.documents.get(uri or "")
    if not doc:
        return None
    name = word_at(doc.text, pos.get("line", 0), pos.get("character", 0))
    if not name:
        return None
    fns, lets = collect_symbols(doc, state)
    sig: Optional[str] = None
    if name in fns:
        sig = fns[name].signature
    elif name in lets:
        sig = lets[name].signature
    elif name in BUILTIN_FUNCTIONS:
        sig = BUILTIN_FUNCTIONS[name] + "  (builtin)"
    if not sig:
        return None
    return {"contents": {"kind": "markdown", "value": f"```nova\n{sig}\n```"}}


# ---------------------------------------------------------------------------
# Completion.
# ---------------------------------------------------------------------------


# LSP CompletionItemKind enum: 3 = Function, 6 = Variable, 14 = Keyword.
COMPLETION_KIND_FN = 3
COMPLETION_KIND_VAR = 6


def handle_completion(
    state: ServerState, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Return a `CompletionList` containing builtin functions, top-level
    `fn` definitions, and top-level `let` definitions visible in `doc`."""
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    items: List[Dict[str, Any]] = []
    if not doc:
        return {"isIncomplete": False, "items": items}

    fns, lets = collect_symbols(doc, state)

    # builtins first (so users discover them in autocomplete)
    for name in sorted(BUILTIN_FUNCTIONS):
        items.append(
            {
                "label": name,
                "kind": COMPLETION_KIND_FN,
                "detail": BUILTIN_FUNCTIONS[name],
                "documentation": {
                    "kind": "markdown",
                    "value": f"Nova builtin: `{BUILTIN_FUNCTIONS[name]}`",
                },
            }
        )

    for name in sorted(fns):
        sym = fns[name]
        items.append(
            {
                "label": name,
                "kind": COMPLETION_KIND_FN,
                "detail": sym.signature,
            }
        )

    for name in sorted(lets):
        sym = lets[name]
        items.append(
            {
                "label": name,
                "kind": COMPLETION_KIND_VAR,
                "detail": sym.signature,
            }
        )

    return {"isIncomplete": False, "items": items}


# ---------------------------------------------------------------------------
# Rename — regex-based identifier replace across open documents + imports.
# ---------------------------------------------------------------------------


def _ident_regex(name: str) -> "re.Pattern[str]":
    return re.compile(r"\b" + re.escape(name) + r"\b")


def _scan_file_for_identifier(
    text: str, name: str
) -> List[Dict[str, Any]]:
    """Return LSP `TextEdit[]` for every `\\bname\\b` match in `text`."""
    edits: List[Dict[str, Any]] = []
    pattern = _ident_regex(name)
    for lineno, line in enumerate(text.splitlines()):
        for m in pattern.finditer(line):
            edits.append(
                {
                    "range": {
                        "start": {"line": lineno, "character": m.start()},
                        "end": {"line": lineno, "character": m.end()},
                    },
                }
            )
    return edits


def _candidate_paths(state: ServerState) -> List[str]:
    """Open-document paths plus their transitively imported files."""
    seen: Set[str] = set()
    out: List[str] = []
    for doc in list(state.documents.values()):
        local = uri_to_path(doc.uri)
        if local and local not in seen:
            seen.add(local)
            out.append(local)
        for imp in _resolved_imports(doc, state):
            if imp not in seen:
                seen.add(imp)
                out.append(imp)
    return out


def handle_rename(
    state: ServerState, params: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    uri = params.get("textDocument", {}).get("uri", "")
    pos = params.get("position", {})
    new_name = params.get("newName") or ""
    doc = state.documents.get(uri)
    if not doc or not new_name:
        return None
    old_name = word_at(doc.text, pos.get("line", 0), pos.get("character", 0))
    if not old_name or old_name == new_name:
        return None
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", new_name):
        return None

    changes: Dict[str, List[Dict[str, Any]]] = {}

    # Open documents are renamed in-buffer (authoritative over disk).
    for d in state.documents.values():
        edits = [
            {"range": e["range"], "newText": new_name}
            for e in _scan_file_for_identifier(d.text, old_name)
        ]
        if edits:
            changes[d.uri] = edits

    # Imported files (on-disk) — only include if not already in open buffers.
    open_paths = {uri_to_path(u) or "" for u in changes}
    for path in _candidate_paths(state):
        if path in open_paths:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        edits = [
            {"range": e["range"], "newText": new_name}
            for e in _scan_file_for_identifier(text, old_name)
        ]
        if edits:
            changes[path_to_uri(path)] = edits

    return {"changes": changes}


# ---------------------------------------------------------------------------
# References.
# ---------------------------------------------------------------------------


def handle_references(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    uri = params.get("textDocument", {}).get("uri", "")
    pos = params.get("position", {})
    doc = state.documents.get(uri)
    if not doc:
        return []
    name = word_at(doc.text, pos.get("line", 0), pos.get("character", 0))
    if not name:
        return []

    locations: List[Dict[str, Any]] = []

    # Open documents (use live buffer text).
    visited_paths: Set[str] = set()
    for d in state.documents.values():
        for e in _scan_file_for_identifier(d.text, name):
            locations.append({"uri": d.uri, "range": e["range"]})
        p = uri_to_path(d.uri)
        if p:
            visited_paths.add(p)

    # Imported files on-disk.
    for path in _candidate_paths(state):
        if path in visited_paths:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for e in _scan_file_for_identifier(text, name):
            locations.append({"uri": path_to_uri(path), "range": e["range"]})
        visited_paths.add(path)

    return locations


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
        "completionProvider": {
            "triggerCharacters": [".", "("],
            "resolveProvider": False,
        },
        "renameProvider": True,
        "referencesProvider": True,
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
    if method == "textDocument/completion":
        result = handle_completion(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/rename":
        result = handle_rename(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/references":
        result = handle_references(state, params)
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
