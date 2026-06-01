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
    * `textDocument/codeAction` (extract function, organize imports,
      sort top-level fn declarations)

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
# Code actions — extract function, organize imports, sort fn declarations.
# ---------------------------------------------------------------------------


# LSP CodeActionKind strings. Using string literals (not an enum) so the
# wire format matches VS Code expectations exactly.
KIND_REFACTOR_EXTRACT = "refactor.extract"
KIND_SOURCE_ORGANIZE_IMPORTS = "source.organizeImports"
KIND_SOURCE_ORGANIZE_FNS = "source.organizeFns"


def _full_doc_range(text: str) -> Dict[str, Any]:
    """LSP range that covers the entire document end-to-end."""
    lines = text.splitlines(keepends=False)
    if not lines:
        return {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 0},
        }
    last_idx = len(lines) - 1
    return {
        "start": {"line": 0, "character": 0},
        "end": {"line": last_idx, "character": len(lines[last_idx])},
    }


def _make_workspace_edit(uri: str, new_text: str, doc_text: str) -> Dict[str, Any]:
    """Build a WorkspaceEdit replacing the entire `uri` document with
    `new_text`. This is the simplest reliable shape — VS Code accepts the
    `changes` form and applies it without diff reconciliation."""
    return {
        "changes": {
            uri: [
                {
                    "range": _full_doc_range(doc_text),
                    "newText": new_text,
                }
            ]
        }
    }


# --- Action 1: Extract function -------------------------------------------


def _find_fn_definitions(text: str) -> List[Tuple[str, int, int, int]]:
    """Locate every top-level `fn name(args) { ... }` definition.

    Returns a list of `(name, start_line, body_open_line, end_line)` tuples
    where `end_line` is the line index of the closing `}` (inclusive).
    Brace counting is done from the opening `{` on the signature line."""
    lines = text.splitlines()
    out: List[Tuple[str, int, int, int]] = []
    i = 0
    while i < len(lines):
        m = FN_DEF_RE.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        # Find the opening `{`. It is usually on the signature line but may
        # be on the next line.
        open_line = i
        while open_line < len(lines) and "{" not in lines[open_line]:
            open_line += 1
        if open_line >= len(lines):
            i += 1
            continue
        depth = 0
        end_line = open_line
        found_open = False
        for j in range(open_line, len(lines)):
            for ch in lines[j]:
                if ch == "{":
                    depth += 1
                    found_open = True
                elif ch == "}":
                    depth -= 1
                    if found_open and depth == 0:
                        end_line = j
                        break
            if found_open and depth == 0:
                end_line = j
                break
        out.append((name, i, open_line, end_line))
        i = end_line + 1
    return out


def _enclosing_fn(
    fns: List[Tuple[str, int, int, int]], line: int
) -> Optional[Tuple[str, int, int, int]]:
    """Smallest function whose body strictly contains `line`."""
    best: Optional[Tuple[str, int, int, int]] = None
    for fn in fns:
        _name, start, _open, end = fn
        if start <= line <= end:
            if best is None or (end - start) < (best[3] - best[1]):
                best = fn
    return best


_LET_BIND_RE = re.compile(r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)")
_KEYWORDS = {
    "fn", "let", "if", "else", "while", "for", "return", "import",
    "true", "false", "nil", "null", "and", "or", "not", "in",
    "break", "continue", "match", "do", "end",
}


def _free_variables(selection: str, available_locals: Set[str]) -> List[str]:
    """Identifiers used in `selection` that are not bound by `let` inside
    the selection itself, not Nova keywords, not literal numbers/strings,
    and not builtin functions. Preserves first-seen order so call sites
    look stable across edits."""
    bound: Set[str] = set(_LET_BIND_RE.findall(selection))
    seen: List[str] = []
    seen_set: Set[str] = set()
    # Strip strings so identifiers inside string literals do not leak in.
    stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', selection)
    for tok in IDENT_RE.findall(stripped):
        if tok in _KEYWORDS:
            continue
        if tok in BUILTIN_FUNCTIONS:
            continue
        if tok in bound:
            continue
        if tok in seen_set:
            continue
        # Only treat as a free variable if it's actually visible at the
        # call site (i.e. listed in `available_locals`). Otherwise it's
        # a global fn name, an unknown symbol, etc — leave it alone.
        if available_locals and tok not in available_locals:
            continue
        seen.append(tok)
        seen_set.add(tok)
    return seen


def _locals_in_scope(fn_lines: List[str], up_to: int) -> Set[str]:
    """Parameters + every `let`-bound name from line 0..up_to-1 of the
    function body (inclusive of params on the signature line)."""
    out: Set[str] = set()
    if not fn_lines:
        return out
    # Parameters: first line is `fn name(a, b, c) {`.
    sig = fn_lines[0]
    pm = FN_DEF_RE.match(sig)
    if pm:
        args = pm.group(2)
        for a in args.split(","):
            a = a.strip()
            if a:
                out.add(a)
    for i in range(min(up_to, len(fn_lines))):
        for nm in _LET_BIND_RE.findall(fn_lines[i]):
            out.add(nm)
    return out


def _last_import_line(lines: List[str]) -> int:
    """Index of the last contiguous-from-top `import "..."` line, or -1
    if there are none."""
    last = -1
    for i, line in enumerate(lines):
        if IMPORT_RE.match(line):
            last = i
            continue
        if line.strip() == "":
            continue
        break
    return last


def _next_extracted_name(text: str) -> str:
    """`extracted_N` where N is one more than the count of existing
    `extracted_*` identifiers (or 1 if none)."""
    matches = re.findall(r"\bextracted_(\d+)\b", text)
    n = max((int(m) for m in matches), default=0) + 1
    return f"extracted_{n}"


def _build_extract_action(
    doc: Document, range_: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """If `range_` covers a usable multi-statement block inside a fn body,
    return a `CodeAction` that extracts it into a top-level helper."""
    lines = doc.text.splitlines()
    start_line = range_.get("start", {}).get("line", 0)
    end_line = range_.get("end", {}).get("line", 0)
    end_char = range_.get("end", {}).get("character", 0)
    # Trim a trailing empty line that VS Code sometimes includes when
    # selecting full lines.
    if end_line > start_line and end_char == 0:
        end_line -= 1
    if end_line < start_line:
        return None
    if not (0 <= start_line < len(lines) and 0 <= end_line < len(lines)):
        return None

    fns = _find_fn_definitions(doc.text)
    enclosing = _enclosing_fn(fns, start_line)
    if not enclosing:
        return None
    fn_name, fn_start, fn_open, fn_end = enclosing
    # Selection must be inside the body (after the opening `{`, before the
    # closing `}`).
    if not (fn_open < start_line and end_line < fn_end):
        return None

    selected_lines = lines[start_line:end_line + 1]
    # Require at least one non-empty selected line.
    if not any(l.strip() for l in selected_lines):
        return None

    # Compute available locals (params + lets defined above the selection).
    fn_body_lines = lines[fn_start:fn_end + 1]
    rel_start = start_line - fn_start
    available = _locals_in_scope(fn_body_lines, rel_start)

    selection_text = "\n".join(selected_lines)
    free_vars = _free_variables(selection_text, available)
    new_name = _next_extracted_name(doc.text)

    # Compute the indentation of the first non-empty selected line so the
    # call-site replacement matches the surrounding style.
    indent = ""
    for l in selected_lines:
        if l.strip():
            indent = l[: len(l) - len(l.lstrip())]
            break

    # Build the new helper function. Indent body by 4 spaces relative to
    # the original selection's indent so it reads as a standalone fn.
    args = ", ".join(free_vars)
    helper_body_lines: List[str] = []
    # Strip common leading indentation from selection so the helper body
    # starts at column 4.
    common = None
    for l in selected_lines:
        if not l.strip():
            continue
        leading = len(l) - len(l.lstrip())
        common = leading if common is None else min(common, leading)
    if common is None:
        common = 0
    for l in selected_lines:
        if l.strip():
            helper_body_lines.append("    " + l[common:])
        else:
            helper_body_lines.append("")
    helper = (
        f"fn {new_name}({args}) {{\n"
        + "\n".join(helper_body_lines)
        + "\n}\n\n"
    )

    # Construct the new document text.
    last_import = _last_import_line(lines)
    insert_at = last_import + 1  # line index where helper is inserted
    # Skip a single blank line directly after the imports so the helper
    # lands in a tidy spot.
    while insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1

    new_lines = list(lines)
    # 1. Replace selected range with a call.
    call_line = f"{indent}{new_name}({args})"
    new_lines[start_line:end_line + 1] = [call_line]
    # 2. Recompute insert_at because we just shrank the list — but only if
    # the selection was below the insertion point.
    if start_line < insert_at:
        removed = (end_line - start_line + 1) - 1
        insert_at -= removed
    # 3. Insert the helper.
    helper_lines = helper.rstrip("\n").split("\n")
    new_lines[insert_at:insert_at] = helper_lines + [""]

    new_text = "\n".join(new_lines)
    # Preserve a trailing newline if the original had one.
    if doc.text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"

    return {
        "title": f"Extract to function `{new_name}`",
        "kind": KIND_REFACTOR_EXTRACT,
        "edit": _make_workspace_edit(doc.uri, new_text, doc.text),
    }


# --- Action 2: Organize imports -------------------------------------------


def _import_group(path: str) -> int:
    """Group key used to bucket imports for sorting."""
    if path.startswith("std/"):
        return 0
    if path.startswith("../src/"):
        return 1
    if path.startswith("../../tests/"):
        return 2
    return 3


def _organize_imports_text(text: str) -> Optional[str]:
    """Return the document with its leading import block sorted+grouped,
    or `None` if no rewrite is needed."""
    lines = text.splitlines()
    # Collect the contiguous import block at the top (blank lines allowed
    # as separators inside the block).
    block_paths: List[str] = []
    last_import_idx = -1
    for i, line in enumerate(lines):
        m = IMPORT_RE.match(line)
        if m:
            block_paths.append(m.group(1))
            last_import_idx = i
            continue
        if line.strip() == "" and last_import_idx == -1:
            # blank line before any import — keep scanning
            continue
        if line.strip() == "" and last_import_idx != -1:
            # blank line between imports is OK
            continue
        # First non-blank, non-import line ends the block.
        break

    if len(block_paths) < 2 and last_import_idx == -1:
        return None  # nothing to do
    if len(block_paths) < 2:
        return None  # only one import — already sorted

    # Sort by (group, path).
    sorted_paths = sorted(block_paths, key=lambda p: (_import_group(p), p))
    # Group block: separate adjacent groups with a blank line.
    rendered: List[str] = []
    prev_group: Optional[int] = None
    for p in sorted_paths:
        g = _import_group(p)
        if prev_group is not None and g != prev_group:
            rendered.append("")
        rendered.append(f'import "{p}"')
        prev_group = g

    # Splice rendered block over the original 0..last_import_idx region.
    new_lines = rendered + lines[last_import_idx + 1:]
    new_text = "\n".join(new_lines)
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"
    if new_text == text:
        return None
    return new_text


def _build_organize_imports_action(doc: Document) -> Optional[Dict[str, Any]]:
    new_text = _organize_imports_text(doc.text)
    if new_text is None:
        return None
    return {
        "title": "Organize imports",
        "kind": KIND_SOURCE_ORGANIZE_IMPORTS,
        "edit": _make_workspace_edit(doc.uri, new_text, doc.text),
    }


# --- Action 3: Sort fn declarations ---------------------------------------


def _is_doc_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("//") or s.startswith("#")


def _collect_fn_blocks(
    text: str,
) -> Tuple[List[Tuple[str, int, int]], List[str]]:
    """Walk `text` and group every top-level fn (with its doc-comment
    prelude) into `(name, block_start_line, block_end_line)` tuples.
    Returns `(blocks, lines)`."""
    lines = text.splitlines()
    defs = _find_fn_definitions(text)
    blocks: List[Tuple[str, int, int]] = []
    for name, start, _open, end in defs:
        # Walk backward to absorb a contiguous block of doc comments.
        block_start = start
        j = start - 1
        while j >= 0 and _is_doc_comment(lines[j]):
            block_start = j
            j -= 1
        blocks.append((name, block_start, end))
    return blocks, lines


def _sort_fns_text(text: str) -> Optional[str]:
    blocks, lines = _collect_fn_blocks(text)
    if len(blocks) < 2:
        return None
    sorted_blocks = sorted(blocks, key=lambda b: b[0])
    if [b[0] for b in blocks] == [b[0] for b in sorted_blocks]:
        return None  # already alphabetical

    # Build the new document: everything outside fn blocks stays in place;
    # fn-block regions are rewritten in sorted order. We walk the blocks
    # in their original positions and replace each region with the
    # sorted-Nth block's lines.
    new_lines: List[str] = []
    i = 0
    block_idx = 0
    blocks_by_start = sorted(blocks, key=lambda b: b[1])
    for orig in blocks_by_start:
        _name, b_start, b_end = orig
        # Emit any pre-block content.
        new_lines.extend(lines[i:b_start])
        # Emit the next sorted block's lines.
        s_name, s_start, s_end = sorted_blocks[block_idx]
        new_lines.extend(lines[s_start:s_end + 1])
        i = b_end + 1
        block_idx += 1
    new_lines.extend(lines[i:])

    new_text = "\n".join(new_lines)
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"
    if new_text == text:
        return None
    return new_text


def _build_sort_fns_action(doc: Document) -> Optional[Dict[str, Any]]:
    new_text = _sort_fns_text(doc.text)
    if new_text is None:
        return None
    return {
        "title": "Sort top-level functions",
        "kind": KIND_SOURCE_ORGANIZE_FNS,
        "edit": _make_workspace_edit(doc.uri, new_text, doc.text),
    }


# --- Top-level dispatcher -------------------------------------------------


def handle_code_action(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    if not doc:
        return []
    rng = params.get("range") or {
        "start": {"line": 0, "character": 0},
        "end": {"line": 0, "character": 0},
    }
    context = params.get("context") or {}
    only = context.get("only")  # list[str] | None

    def _allowed(kind: str) -> bool:
        if not only:
            return True
        # LSP CodeActionKind hierarchy: prefix-matches are accepted.
        return any(kind == k or kind.startswith(k + ".") for k in only)

    actions: List[Dict[str, Any]] = []

    if _allowed(KIND_REFACTOR_EXTRACT):
        extract = _build_extract_action(doc, rng)
        if extract:
            actions.append(extract)
    if _allowed(KIND_SOURCE_ORGANIZE_IMPORTS):
        oi = _build_organize_imports_action(doc)
        if oi:
            actions.append(oi)
    if _allowed(KIND_SOURCE_ORGANIZE_FNS):
        sf = _build_sort_fns_action(doc)
        if sf:
            actions.append(sf)

    return actions


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
        "codeActionProvider": {
            "codeActionKinds": [
                KIND_REFACTOR_EXTRACT,
                KIND_SOURCE_ORGANIZE_IMPORTS,
                KIND_SOURCE_ORGANIZE_FNS,
            ],
        },
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
    if method == "textDocument/codeAction":
        result = handle_code_action(state, params)
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
