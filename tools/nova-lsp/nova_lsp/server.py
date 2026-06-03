"""Nova LSP server.

Implements a subset of the Language Server Protocol over stdio
using only the Python standard library. Supports:

    * `initialize`, `initialized`, `shutdown`, `exit`
    * `textDocument/didOpen`, `didChange`, `didSave`, `didClose`
    * `textDocument/publishDiagnostics` (driven by `nova --check`)
    * `textDocument/hover` (symbol scan of `import`-ed runtime files)
    * `textDocument/completion` (builtins + fn/let from doc + imports)
    * `textDocument/definition` (intra-file + transitively imported fns/lets)
    * `textDocument/rename` (workspace-wide for top-level fn/let/const/
      type via R9C's rename_workspace; single-buffer for locals + params)
    * `textDocument/references` (regex-based occurrence scan over the
      transitive import graph)
    * `textDocument/codeAction` (extract function, organize imports,
      sort top-level fn declarations)
    * `workspace/symbol` (fuzzy name search across all indexed `.nova`
      files; index is warmed incrementally on didOpen/didChange and
      lazily crawls the workspace root on first query)
    * `textDocument/semanticTokens/full` + `/range` (per-token
      classification beyond TextMate: variables vs constants vs
      functions vs types, declaration vs reference, readonly + static
      modifiers, namespace tagging for import paths)
    * `textDocument/prepareCallHierarchy` +
      `callHierarchy/incomingCalls` + `callHierarchy/outgoingCalls`
      (caller / callee navigation rendered as a tree in the editor;
      reuses R8C's workspace symbol index + R9C's reference scanner)

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
from nova_lsp.call_hierarchy import (
    incoming_calls,
    outgoing_calls,
    prepare_call_hierarchy,
)
from nova_lsp.hover_docs import (
    extract_doc_comment,
    extract_doc_comment_from_text,
    render_hover_markdown,
)
from nova_lsp.imports import FileCache, find_definition, walk_imports
from nova_lsp.rename_workspace import (
    build_workspace_edit,
    classify_symbol,
    plan_workspace_rename,
)
from nova_lsp.semantic_tokens import (
    SemanticTokenizer,
    semantic_tokens_legend,
    tokens_to_lsp_array,
)
from nova_lsp.workspace_symbols import (
    SYMBOL_KIND_CONSTANT,
    SYMBOL_KIND_FUNCTION,
    WorkspaceSymbolIndex,
)

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
    # Shared file-scan cache used by definition/references. Imports are
    # cheap to scan but we re-walk the graph on every hover/completion
    # too, so keeping a mtime-keyed cache is a clear win.
    file_cache: "FileCache" = field(default_factory=FileCache)
    # Workspace-wide symbol index used by `workspace/symbol`. Files are
    # added to it on `didOpen`/`didChange` so live edits are reflected
    # immediately; the workspace root is lazily crawled on the first
    # `workspace/symbol` request so server startup stays fast.
    workspace_symbols: "WorkspaceSymbolIndex" = field(default_factory=WorkspaceSymbolIndex)


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
#
# The hover handler resolves the word under the cursor to a NOVA fn / let
# / const / type (or builtin), then enriches the response with the `///`
# doc-comment block above the declaration. Doc extraction is delegated
# to `hover_docs.extract_doc_comment*` so the stop-rule (contiguous,
# stop-at-blank) lives in one well-tested place.
#
# Cross-file behavior: when the cursor sits on `foo` in `A.nova` and
# `foo` is defined in `B.nova`, the docs come from B — we use R5F's
# `find_definition` over the live import graph (with open buffers
# treated as authoritative via `_text_overrides`) so unsaved edits to
# the declaration site are still surfaced.
# ---------------------------------------------------------------------------


def _resolve_definition_for_hover(
    state: ServerState, doc: Document, name: str
) -> Optional[Tuple[str, int]]:
    """Locate the file + zero-based line where `name` is declared.

    Returns `(abs_path, def_line)` for the declaration site (used by the
    doc-comment extractor), or `None` if the symbol isn't a top-level
    fn/let reachable from this document's import graph. Builtins return
    `None` — they have no source line.
    """
    start = uri_to_path(doc.uri)
    if not start:
        return None
    hit = find_definition(
        name,
        os.path.abspath(start),
        state.file_cache,
        text_overrides=_text_overrides(state),
    )
    if hit is None:
        return None
    path, (line, _c0, _c1) = hit
    return path, line


def _doc_comment_for(
    state: ServerState, def_path: str, def_line: int
) -> str:
    """Extract the `///` doc block above `def_line` in `def_path`.

    Honours live buffer overrides: when the declaration's file is open
    in the editor we read from the buffer text (not disk) so edits to
    the docs are reflected in hover immediately, before the user saves.
    """
    abs_path = os.path.abspath(def_path)
    overrides = _text_overrides(state)
    if abs_path in overrides:
        return extract_doc_comment_from_text(overrides[abs_path], def_line)
    return extract_doc_comment(abs_path, def_line)


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
    # Look up the declaration site so we can pull `///` docs above it.
    # Builtins (no source location) and unresolved symbols silently skip
    # this step — the hover still renders the signature.
    docs = ""
    if name not in BUILTIN_FUNCTIONS:
        hit = _resolve_definition_for_hover(state, doc, name)
        if hit is not None:
            def_path, def_line = hit
            docs = _doc_comment_for(state, def_path, def_line)
    return {"contents": {"kind": "markdown", "value": render_hover_markdown(sig, docs)}}


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
    """Dispatch rename requests.

    For a top-level `fn` / `let` / `const` / `type` we route through
    `handle_rename_workspace` (R9C), which uses the workspace symbol
    index + import-graph reachability to rename every file in the
    workspace that imports the definition site. For local bindings
    (function parameters, indented `let`s, anonymous helpers) we keep
    the legacy in-buffer regex rename below — those don't propagate
    across files.

    Returns either:
      * a WorkspaceEdit `{"changes": {uri: [TextEdit, ...]}}`, OR
      * `None` when the rename is a no-op or invalid input.
    """
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

    # Try the workspace-wide path first (top-level symbols only).
    workspace_result = handle_rename_workspace(
        state, doc, old_name, new_name
    )
    if workspace_result is not None:
        return workspace_result

    # Fall back to the legacy single-buffer-plus-imports rename. This
    # covers local bindings and the case where the symbol is unknown to
    # the import graph (e.g. completely intra-buffer use).
    return _handle_rename_legacy(state, old_name, new_name)


def handle_rename_workspace(
    state: ServerState,
    doc: Document,
    old_name: str,
    new_name: str,
) -> Optional[Dict[str, Any]]:
    """Workspace-wide rename routed through R9C's `rename_workspace`.

    Returns:
      * a WorkspaceEdit dict when the rename is a top-level fn/let/
        const/type with no name conflicts;
      * an LSP `ResponseError` dict (caller propagates) when the new
        name would clash with an existing top-level decl;
      * `None` when the symbol is a local binding so the caller can
        fall back to the legacy rename path.
    """
    start = uri_to_path(doc.uri)
    if not start:
        return None
    overrides = _text_overrides(state)
    hit = find_definition(
        old_name,
        os.path.abspath(start),
        state.file_cache,
        text_overrides=overrides,
    )
    if hit is None:
        return None
    def_path, (def_line, _c0, _c1) = hit
    kind = classify_symbol(
        old_name, def_path, def_line, state.file_cache, overrides
    )
    if kind != "toplevel":
        return None
    # Make sure the workspace index has seen the project root before we
    # ask it for the file list — first-time queries lazy-crawl. When
    # the client didn't pass a rootPath at initialize time (e.g.
    # `code path/to/file.nova` opens a single file without a folder),
    # we lazily crawl the open documents' parent directories so
    # sibling .nova files are still discovered.
    crawled_roots: Set[str] = set()
    if state.root_path:
        state.workspace_symbols.index_workspace_root(state.root_path)
        crawled_roots.add(os.path.abspath(state.root_path))
    # Live buffers should be indexed too so renames pick up unsaved
    # files. Open-doc import closures are also added to the candidate
    # list — that's the failsafe path when neither the workspace root
    # nor a sibling crawl yields the importer file.
    extra_paths: List[str] = []
    seen_paths: Set[str] = set()
    for d in state.documents.values():
        p = uri_to_path(d.uri)
        if not p:
            continue
        abs_p = os.path.abspath(p)
        if abs_p not in seen_paths:
            extra_paths.append(abs_p)
            seen_paths.add(abs_p)
        state.workspace_symbols.index_text(p, d.text)
        # Fallback: crawl the parent dir when no workspace root was
        # provided. This is a one-shot per-directory walk guarded by
        # the WorkspaceSymbolIndex's `_crawled_roots` set.
        parent_dir = os.path.dirname(abs_p)
        if parent_dir and parent_dir not in crawled_roots:
            state.workspace_symbols.index_workspace_root(parent_dir)
            crawled_roots.add(parent_dir)
        # Also seed `extra_paths` with the open doc's import closure —
        # belt-and-suspenders for files outside any crawled root.
        for entry in walk_imports(
            abs_p,
            state.file_cache,
            text_overrides=overrides,
        ):
            if entry.path not in seen_paths:
                extra_paths.append(entry.path)
                seen_paths.add(entry.path)
    plan = plan_workspace_rename(
        old_name,
        new_name,
        def_path,
        state.file_cache,
        state.workspace_symbols,
        extra_paths=extra_paths,
        text_overrides=overrides,
    )
    if plan.conflict_message:
        # Encode the conflict as a sentinel the dispatcher turns into a
        # JSON-RPC ResponseError. We use a non-standard shape with an
        # `_rename_conflict` key so it isn't mistaken for an empty edit.
        return {"_rename_conflict": plan.conflict_message}
    if not plan.references:
        return None
    return build_workspace_edit(plan.references, new_name)


def _handle_rename_legacy(
    state: ServerState, old_name: str, new_name: str
) -> Dict[str, Any]:
    """Legacy single-buffer (+ open-doc transitive imports) rename.

    Used as the fallback for local bindings — function parameters,
    inner `let` declarations, anonymous helpers. Renames only the
    bindings that are visible in already-open buffers + their
    transitive on-disk imports, NOT every file in the workspace.
    """
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
# Definition + References.
#
# Both walk the transitive import graph rooted at the current document via
# `walk_imports`, which uses an mtime-keyed FileCache. Open documents are
# fed in as `text_overrides` so live buffer edits stay authoritative even
# before the user has saved.
# ---------------------------------------------------------------------------


def _text_overrides(state: ServerState) -> Dict[str, str]:
    """Map of absolute path -> live buffer text for every open document.
    Used by `walk_imports` so unsaved edits beat stale on-disk content."""
    overrides: Dict[str, str] = {}
    for d in state.documents.values():
        p = uri_to_path(d.uri)
        if p:
            overrides[os.path.abspath(p)] = d.text
    return overrides


def _invalidate_for(state: ServerState, uri: str) -> None:
    """Drop the cached scan for `uri` so the next definition/references
    call re-reads the file (either from the live buffer or from disk).
    Called on every didOpen/didChange/didSave/didClose."""
    p = uri_to_path(uri)
    if p:
        state.file_cache.invalidate(p)


def handle_definition(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Resolve the identifier under the cursor.

    Strategy:
      1. Read the word under the cursor.
      2. Walk the import graph (start file first) for a top-level
         `fn name(...)` or `let name = ...` matching that word.
      3. Return the matched name's source span as an LSP `Location[]`,
         or an empty list if the identifier is unknown (builtins return
         empty because they have no source location)."""
    uri = params.get("textDocument", {}).get("uri", "")
    pos = params.get("position", {})
    doc = state.documents.get(uri)
    if not doc:
        return []
    name = word_at(doc.text, pos.get("line", 0), pos.get("character", 0))
    if not name:
        return []

    start = uri_to_path(doc.uri)
    if not start:
        return []
    hit = find_definition(
        name,
        os.path.abspath(start),
        state.file_cache,
        text_overrides=_text_overrides(state),
    )
    if hit is None:
        # Unknown identifier (likely a builtin or undefined symbol).
        # Builtins have no source location — return empty so the editor
        # can fall back to hover for the signature.
        return []
    path, (line, c0, c1) = hit
    return [{
        "uri": path_to_uri(path),
        "range": {
            "start": {"line": line, "character": c0},
            "end": {"line": line, "character": c1},
        },
    }]


def handle_references(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Find every `\\bname\\b` match across the import graph.

    Walks `walk_imports` from the current doc (so the start file plus every
    reachable import is scanned). Open documents contribute their live
    buffer text via `text_overrides`. Also includes other open documents
    that aren't reachable from the start file — they're independent roots
    in a multi-buffer workspace, so users still expect references there."""
    uri = params.get("textDocument", {}).get("uri", "")
    pos = params.get("position", {})
    doc = state.documents.get(uri)
    if not doc:
        return []
    name = word_at(doc.text, pos.get("line", 0), pos.get("character", 0))
    if not name:
        return []

    locations: List[Dict[str, Any]] = []
    overrides = _text_overrides(state)
    visited: Set[str] = set()

    # Walk from the current doc first.
    start = uri_to_path(doc.uri)
    if start:
        entries = walk_imports(
            os.path.abspath(start),
            state.file_cache,
            text_overrides=overrides,
            visited=visited,
        )
        for entry in entries:
            for e in _scan_file_for_identifier(entry.text, name):
                locations.append({
                    "uri": path_to_uri(entry.path),
                    "range": e["range"],
                })

    # Other open documents (and their import closures) — independent roots
    # that aren't reachable from the current doc.
    for d in state.documents.values():
        p = uri_to_path(d.uri)
        if not p:
            continue
        abs_p = os.path.abspath(p)
        if abs_p in visited:
            continue
        entries = walk_imports(
            abs_p,
            state.file_cache,
            text_overrides=overrides,
            visited=visited,
        )
        for entry in entries:
            for e in _scan_file_for_identifier(entry.text, name):
                locations.append({
                    "uri": path_to_uri(entry.path),
                    "range": e["range"],
                })

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
# Workspace symbols — fuzzy search across every indexed `.nova` file.
#
# Strategy: incremental indexing on `didOpen` / `didChange` / `didSave`,
# plus a one-time lazy crawl of `state.root_path` on the first
# `workspace/symbol` request. Open-document text always overrides what
# the crawl found on disk so unsaved edits are searchable immediately.
# ---------------------------------------------------------------------------


def _refresh_workspace_symbols_for_doc(state: ServerState, doc: Document) -> None:
    """Re-index `doc` in the workspace symbol table from its live buffer.

    Called on every document lifecycle event so the workspace picker
    stays in sync with the editor."""
    p = uri_to_path(doc.uri)
    if not p:
        return
    state.workspace_symbols.index_text(p, doc.text)


def _drop_workspace_symbols_for_uri(state: ServerState, uri: str) -> None:
    """Remove `uri`'s symbols from the workspace index — but only if the
    document is being closed *and* the file is no longer on disk. If the
    file still exists we re-read it so closed-but-saved files stay
    searchable from the workspace picker."""
    p = uri_to_path(uri)
    if not p:
        return
    if os.path.isfile(p):
        state.workspace_symbols.index_file(p)
    else:
        state.workspace_symbols.invalidate_file(p)


def handle_workspace_symbol(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return the top fuzzy matches for `params.query` across the
    workspace. The first call lazily crawls `state.root_path` if it
    hasn't been indexed yet; subsequent calls reuse the warm index."""
    query = (params.get("query") or "").strip()
    if state.root_path:
        state.workspace_symbols.index_workspace_root(state.root_path)
    # Make sure live buffers are present in the index — they are kept
    # current by didOpen/didChange handlers, but double-tap here so the
    # first query after `initialize` doesn't miss them.
    for doc in state.documents.values():
        _refresh_workspace_symbols_for_doc(state, doc)
    entries = state.workspace_symbols.fuzzy_match(query, limit=100)
    return [e.to_symbol_information() for e in entries]


# ---------------------------------------------------------------------------
# Semantic tokens — `textDocument/semanticTokens/full` and `/range`.
#
# Returns a delta-compressed flat int array classifying every identifier,
# string, number, and keyword in the buffer. The legend (token-type names
# + modifier names) is advertised at `initialize` time via
# `server_capabilities()` and must be referenced symmetrically by the
# client.
#
# Cross-file context: we use R8C's WorkspaceSymbolIndex to look up
# already-indexed `fn` / `const` declarations from sibling files so a
# bare identifier like `compute_total` (no parens, no local declaration)
# still gets classified as `function` when it's defined elsewhere in the
# workspace. The workspace index is warmed on the first
# `workspace/symbol` query and on every didOpen/didChange, so by the
# time the client asks for semantic tokens the cross-file picture is
# usually ready.
# ---------------------------------------------------------------------------


def _known_symbol_names(state: ServerState) -> Tuple[Set[str], Set[str], Set[str]]:
    """Walk the warm workspace symbol index and return three sets:
    (function names, type names, constant names). Used to seed the
    semantic tokenizer with cross-file knowledge.

    Type names: NOVA's index doesn't currently emit a SymbolKind for
    type declarations (they show up as Function/Variable depending on the
    decl keyword), so this set is empty for now. We keep the slot for
    forward compatibility when R8C-or-similar adds a Type kind.
    """
    fn_names: Set[str] = set()
    const_names: Set[str] = set()
    for entry in state.workspace_symbols.all_symbols():
        if entry.kind == SYMBOL_KIND_FUNCTION:
            fn_names.add(entry.name)
        elif entry.kind == SYMBOL_KIND_CONSTANT:
            const_names.add(entry.name)
    return fn_names, set(), const_names


def handle_semantic_tokens_full(
    state: ServerState, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Return the full semantic-token array for the document under `uri`.

    Response shape per LSP spec: `{"data": [int, int, int, int, int, ...]}`.
    The five-int-per-token encoding is documented in semantic_tokens.py."""
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    if not doc:
        return {"data": []}
    # Warm the workspace index so cross-file fn/const refs classify well.
    # Cheap when already warm (lazy crawls are guarded by _crawled_roots).
    if state.root_path:
        state.workspace_symbols.index_workspace_root(state.root_path)
    for d in state.documents.values():
        _refresh_workspace_symbols_for_doc(state, d)
    known_fns, known_types, known_consts = _known_symbol_names(state)
    tokenizer = SemanticTokenizer(
        doc.text,
        known_functions=known_fns,
        known_types=known_types,
        known_constants=known_consts,
    )
    tokens = tokenizer.tokenize()
    return {"data": tokens_to_lsp_array(tokens)}


def handle_semantic_tokens_range(
    state: ServerState, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Return semantic tokens for the lines covered by `params.range`.

    The whole document is still tokenized internally — semantic-tokens
    classification depends on top-level context (which fns are declared,
    etc) — but we filter the output to tokens whose line falls inside
    the requested range. This gives clients a cheaper payload for
    initial paints in big files while preserving classification quality.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    rng = params.get("range") or {}
    doc = state.documents.get(uri)
    if not doc:
        return {"data": []}
    start_line = rng.get("start", {}).get("line", 0)
    end_line = rng.get("end", {}).get("line", 0)
    if start_line > end_line:
        return {"data": []}
    if state.root_path:
        state.workspace_symbols.index_workspace_root(state.root_path)
    for d in state.documents.values():
        _refresh_workspace_symbols_for_doc(state, d)
    known_fns, known_types, known_consts = _known_symbol_names(state)
    tokenizer = SemanticTokenizer(
        doc.text,
        known_functions=known_fns,
        known_types=known_types,
        known_constants=known_consts,
    )
    all_tokens = tokenizer.tokenize()
    in_range = [t for t in all_tokens if start_line <= t.line <= end_line]
    return {"data": tokens_to_lsp_array(in_range)}


# ---------------------------------------------------------------------------
# Call hierarchy — `textDocument/prepareCallHierarchy`,
# `callHierarchy/incomingCalls`, `callHierarchy/outgoingCalls`.
#
# The classifier and workspace walk live in `call_hierarchy.py`. The
# handlers here glue LSP request params to the module's API and ensure
# the workspace index + buffer overrides are warmed before each call.
# ---------------------------------------------------------------------------


def _warm_workspace_for_call_hierarchy(state: ServerState) -> List[str]:
    """Make sure the workspace symbol index has seen the project root
    + every open buffer, returning the union of open-doc paths and
    their transitive import closures so call-hierarchy scans don't
    miss files outside the indexed workspace root.

    Mirrors the warming used by `handle_rename_workspace` — the call-
    hierarchy module's `incoming_calls` candidate set is structured
    the same way (indexed files + extras).
    """
    overrides = _text_overrides(state)
    if state.root_path:
        state.workspace_symbols.index_workspace_root(state.root_path)
    extra_paths: List[str] = []
    seen: Set[str] = set()
    crawled_roots: Set[str] = set()
    if state.root_path:
        crawled_roots.add(os.path.abspath(state.root_path))
    for d in state.documents.values():
        p = uri_to_path(d.uri)
        if not p:
            continue
        abs_p = os.path.abspath(p)
        if abs_p not in seen:
            extra_paths.append(abs_p)
            seen.add(abs_p)
        state.workspace_symbols.index_text(p, d.text)
        parent_dir = os.path.dirname(abs_p)
        if parent_dir and parent_dir not in crawled_roots:
            state.workspace_symbols.index_workspace_root(parent_dir)
            crawled_roots.add(parent_dir)
        for entry in walk_imports(
            abs_p,
            state.file_cache,
            text_overrides=overrides,
        ):
            if entry.path not in seen:
                extra_paths.append(entry.path)
                seen.add(entry.path)
    return extra_paths


def handle_prepare_call_hierarchy(
    state: ServerState, params: Dict[str, Any]
) -> Optional[List[Dict[str, Any]]]:
    """Resolve the cursor's symbol into a CallHierarchyItem[].

    Returns either:
      * `None` -> the cursor isn't on a recognized top-level fn name
        (LSP clients render this as "no call hierarchy available").
      * `[CallHierarchyItem]` -> the single-element list the editor
        then passes to `incomingCalls`/`outgoingCalls`.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    pos = params.get("position", {}) or {}
    doc = state.documents.get(uri)
    if not doc:
        return None
    # Warm so cross-file resolution (Step 4 in prepare) sees siblings.
    _warm_workspace_for_call_hierarchy(state)
    return prepare_call_hierarchy(
        uri=uri,
        line=pos.get("line", 0),
        character=pos.get("character", 0),
        doc_text=doc.text,
        file_cache=state.file_cache,
        workspace_index=state.workspace_symbols,
        text_overrides=_text_overrides(state),
    )


def handle_incoming_calls(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return every fn that CALLS `params.item`.

    LSP shape: `{"item": CallHierarchyItem}` in, `CallHierarchyIncomingCall[]`
    out. Empty list when no caller is found (private/unused fn).
    """
    item = params.get("item") or {}
    if not item.get("name"):
        return []
    extra_paths = _warm_workspace_for_call_hierarchy(state)
    return incoming_calls(
        item,
        state.workspace_symbols,
        state.file_cache,
        extra_paths=extra_paths,
        text_overrides=_text_overrides(state),
    )


def handle_outgoing_calls(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return every fn that `params.item` CALLS.

    LSP shape: `{"item": CallHierarchyItem}` in, `CallHierarchyOutgoingCall[]`
    out. Empty list for leaf fns (no calls inside the body) or when the
    body can't be located.
    """
    item = params.get("item") or {}
    if not item.get("name"):
        return []
    _warm_workspace_for_call_hierarchy(state)
    return outgoing_calls(
        item,
        state.file_cache,
        workspace_index=state.workspace_symbols,
        text_overrides=_text_overrides(state),
    )


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
        "definitionProvider": True,
        "renameProvider": True,
        "referencesProvider": True,
        "codeActionProvider": {
            "codeActionKinds": [
                KIND_REFACTOR_EXTRACT,
                KIND_SOURCE_ORGANIZE_IMPORTS,
                KIND_SOURCE_ORGANIZE_FNS,
            ],
        },
        "workspaceSymbolProvider": {"resolveProvider": False},
        "semanticTokensProvider": {
            "legend": semantic_tokens_legend(),
            "range": True,
            "full": True,
        },
        "callHierarchyProvider": True,
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
        _invalidate_for(state, doc.uri)
        _refresh_workspace_symbols_for_doc(state, doc)
        publish_diagnostics(state, doc, out_stream)
        return True
    if method == "textDocument/didChange":
        uri = params.get("textDocument", {}).get("uri", "")
        changes = params.get("contentChanges") or []
        doc = state.documents.get(uri)
        if doc and changes:
            doc.text = changes[-1].get("text", doc.text)
            doc.version = params.get("textDocument", {}).get("version", doc.version + 1)
            _invalidate_for(state, uri)
            _refresh_workspace_symbols_for_doc(state, doc)
            publish_diagnostics(state, doc, out_stream)
        return True
    if method == "textDocument/didSave":
        uri = params.get("textDocument", {}).get("uri", "")
        doc = state.documents.get(uri)
        if doc:
            new_text = params.get("text")
            if isinstance(new_text, str):
                doc.text = new_text
            _invalidate_for(state, uri)
            _refresh_workspace_symbols_for_doc(state, doc)
            publish_diagnostics(state, doc, out_stream)
        return True
    if method == "textDocument/didClose":
        uri = params.get("textDocument", {}).get("uri", "")
        state.documents.pop(uri, None)
        _invalidate_for(state, uri)
        _drop_workspace_symbols_for_uri(state, uri)
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
        if isinstance(result, dict) and "_rename_conflict" in result:
            # R9C name-conflict path — surface as a JSON-RPC ResponseError
            # (code -32803 = LSP "Request failed", a non-fatal client error
            # that VS Code renders as a popup without tearing down the
            # session). Falling through to `make_response` with `None`
            # would silently drop the edit instead.
            write_message(
                out_stream,
                make_error(req_id, -32803, result["_rename_conflict"]),
            )
            return True
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/definition":
        result = handle_definition(state, params)
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
    if method == "workspace/symbol":
        result = handle_workspace_symbol(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/semanticTokens/full":
        result = handle_semantic_tokens_full(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/semanticTokens/range":
        result = handle_semantic_tokens_range(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/prepareCallHierarchy":
        result = handle_prepare_call_hierarchy(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "callHierarchy/incomingCalls":
        result = handle_incoming_calls(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "callHierarchy/outgoingCalls":
        result = handle_outgoing_calls(state, params)
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
