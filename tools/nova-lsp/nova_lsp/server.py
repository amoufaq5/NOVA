"""Nova LSP server.

Implements a subset of the Language Server Protocol over stdio
using only the Python standard library. Supports:

    * `initialize`, `initialized`, `shutdown`, `exit`
    * `textDocument/didOpen`, `didChange`, `didSave`, `didClose`
    * `textDocument/publishDiagnostics` (driven by `nova --check`)
    * `textDocument/hover` (symbol scan of `import`-ed runtime files)
    * `textDocument/completion` (builtins + fn/let from doc + imports;
      R24E adds context-aware suggestions: `Name::` -> enum variants,
      `var.` -> struct fields, `let x: ` / fn-param `(p: ` / `Box<` ->
      enum + struct + primitive type names; falls back to the legacy
      text-based list when no trigger applies)
    * `textDocument/definition` (intra-file + transitively imported fns/lets)
    * `textDocument/prepareRename` (R32E — validates the cursor sits on
      a renameable identifier before the editor pops the rename dialog;
      returns `{range, placeholder}` or `null` for keyword / comment /
      string / whitespace positions)
    * `textDocument/rename` (workspace-wide for top-level fn/let/const/
      type via R9C's rename_workspace; scope-confined for locals + params
      via R32E's `enclosing_fn_range` walker; invalid new names — bad
      shape, NOVA keywords — return JSON-RPC `-32602 Invalid Params`)
    * `textDocument/references` (regex-based occurrence scan over the
      transitive import graph)
    * `textDocument/codeAction` (extract function, organize imports,
      sort top-level fn declarations, plus a `quickfix` action that
      auto-adds missing match arms when R17A's exhaustiveness WARN
      fires on a non-exhaustive match)
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
    * `textDocument/inlayHint` (parameter-name ghost text at call
      sites + literal-RHS type hints on `let` bindings; reuses R5F's
      `find_definition` to resolve the called fn's declared params)
    * `textDocument/codeLens` + `codeLens/resolve` (annotations above
      top-level declarations — "N references" on fn / let / const,
      "N variants used" on enum, with an optional "/ tested" marker
      when a `tests/test_*.nova` mentions the declaration; R33D layers
      "▶ Run / ⏷ Debug" lenses on top-level `fn test_*` decls that
      dispatch to client-side `nova-lsp.runTest` / `nova-lsp.debugTest`
      commands)
    * `textDocument/documentLink` (R33D — each `import "path/file.nova"`
      becomes a clickable link with the resolved `file://` URI; relative
      paths anchor on the current file's directory, absolute paths pass
      through unchanged, dead-link UX is the editor's job)
    * `textDocument/prepareTypeHierarchy` +
      `typeHierarchy/supertypes` + `typeHierarchy/subtypes` (navigate
      sub/supertype edges — enum -> variants, type alias -> RHS base
      and other aliases pointing back at it, cross-file via imports +
      workspace index; reuses R8C's symbol index for the candidate set)
    * `textDocument/foldingRange` (collapse/expand markers in the
      editor gutter — fn bodies, match / if / else blocks, enum +
      struct bodies, contiguous ``///`` doc comment blocks, contiguous
      import blocks)
    * `textDocument/documentSymbol` (file outline tree shown in the
      editor sidebar — hierarchical `DocumentSymbol[]` with each
      top-level fn / let / const / type / enum / struct as a node, and
      enum variants / struct fields nested as children)
    * `workspace/diagnostic` (pull-model workspace-wide diagnostic
      aggregation per LSP 3.17 — single panel feed of all diagnostic
      markers across every indexed file + open buffer, with
      content-hash `resultId` for incremental `kind: "unchanged"` vs
      `kind: "full"` reports; enhances the existing diagnostic
      capability rather than adding a top-level provider)

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
from nova_lsp.code_lens import compute_code_lenses, resolve_code_lens
from nova_lsp.document_link import compute_document_links
from nova_lsp.document_symbols import compute_document_symbols
from nova_lsp.exhaustiveness_fix import (
    KIND_QUICKFIX,
    build_exhaustiveness_code_actions,
)
from nova_lsp.extract_function import (
    build_extract_action as build_extract_function_action,
)
from nova_lsp.folding_ranges import compute_folding_ranges
from nova_lsp.hover_docs import (
    extract_doc_comment,
    extract_doc_comment_from_text,
    render_hover_markdown,
)
from nova_lsp.imports import FileCache, find_definition, walk_imports
from nova_lsp.inlay_hints import compute_inlay_hints
from nova_lsp.inline_variable import (
    KIND_REFACTOR_INLINE,
    build_inline_action,
)
from nova_lsp.prepare_rename import (
    detect_same_scope_shadow,
    enclosing_fn_range,
    identifier_range_at,
    is_keyword,
    scope_constrained_occurrences,
    validate_new_name,
)
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
from nova_lsp.type_completion import (
    TRIGGER_NONE,
    compute_type_aware_completions,
    detect_trigger,
)
from nova_lsp.type_hierarchy import (
    prepare_type_hierarchy,
    subtypes,
    supertypes,
)
from nova_lsp.workspace_diagnostics import (
    compute_workspace_diagnostics,
    parse_previous_result_ids,
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


def _workspace_diagnostic_runner(state: ServerState):
    """Build a closure that runs the per-file diagnostic engine.

    The workspace_diagnostics module is intentionally decoupled from
    the server's compiler-shelling logic so its unit tests can inject
    deterministic stubs. This adapter ties it back into the live
    `nova --check` flow at runtime by wrapping `run_compiler_check`
    in a path-aware shim.
    """
    def runner(path: str, text: str):
        # `run_compiler_check` works in terms of a Document so it can
        # write the buffer text to a tempfile and run the compiler over
        # it. Re-using that path (rather than reading `path` from disk)
        # is intentional -- it lets the workspace check see unsaved
        # buffer edits without round-tripping through the filesystem.
        doc = Document(uri=path_to_uri(path), text=text)
        return run_compiler_check(state, doc)
    return runner


def handle_workspace_diagnostic(
    state: ServerState, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Handler for `workspace/diagnostic`.

    Walks every file in the workspace symbol index plus every open
    document buffer, running the diagnostic engine on each. Returns
    a WorkspaceDiagnosticReport in the LSP 3.17 wire shape:
    `{items: WorkspaceDocumentDiagnosticReport[]}`.

    Incremental support: the client may pass
    `previousResultIds: [{uri, value}, ...]`. Files whose current
    content hash matches the previousResultId return
    `kind: "unchanged"` (saving the client + server an item payload);
    the rest get a fresh `kind: "full"` report.

    Lazy crawl: the index is warmed up exactly once -- if no files
    have been indexed yet (e.g. the client requests
    workspace/diagnostic before any workspace/symbol query), we walk
    the workspace root on the spot so the panel has something to
    show on the very first request.
    """
    if state.root_path:
        # Warm the index on first request so the panel isn't empty
        # before the user has run a workspace/symbol query.
        state.workspace_symbols.index_workspace_root(state.root_path)

    document_overrides = {uri: doc.text for uri, doc in state.documents.items()}
    document_versions = {uri: doc.version for uri, doc in state.documents.items()}
    prev_ids = parse_previous_result_ids(params.get("previousResultIds"))

    return compute_workspace_diagnostics(
        file_cache=state.file_cache,
        workspace_index=state.workspace_symbols,
        diagnostic_runner=_workspace_diagnostic_runner(state),
        document_overrides=document_overrides,
        document_versions=document_versions,
        previous_result_ids=prev_ids,
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
    """Return a `CompletionList` for the cursor at ``params.position``.

    Behaviour is two-layered:

      1. *Type-aware*: if the cursor follows a recognised trigger
         (``Name::``, ``var.``, ``let x: ``, fn-param ``(p: ``,
         ``Box<``) we return a focused list — enum variants, struct
         fields, or known type names. The generic builtin / fn / let
         list is NOT appended in this case because mixing in unrelated
         entries would defeat the trigger's purpose. (R24E)
      2. *Text-based fallback*: otherwise we return the legacy union of
         builtins + top-level ``fn`` / ``let`` reachable from the open
         buffer. The editor filters this against the typed prefix."""
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    items: List[Dict[str, Any]] = []
    if not doc:
        return {"isIncomplete": False, "items": items}

    # --- R24E: type-aware completion (preempts the generic list) ----
    position = params.get("position") or {}
    if state.root_path:
        # Keep the workspace index warm so cross-file lookups work
        # without waiting for the user to open the donor file.
        state.workspace_symbols.index_workspace_root(state.root_path)
    type_aware = compute_type_aware_completions(
        uri,
        position,
        doc.text,
        state.file_cache,
        workspace_index=state.workspace_symbols,
        text_overrides=_text_overrides(state),
    )
    if type_aware is not None:
        # Trigger recognised — return the focused list verbatim.
        return {"isIncomplete": False, "items": type_aware}

    # --- Generic fallback: builtins + user fn/let -------------------
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

    R32E extends the original dispatcher with three pre-flight checks
    matching the LSP `textDocument/rename` spec:

      1. The new name must be a syntactically valid NOVA identifier.
      2. The new name must not be a reserved NOVA keyword.
      3. The new name should not silently shadow an existing same-scope
         binding — we attach a warning string but still emit the edit so
         the user can decide.

    For a top-level `fn` / `let` / `const` / `type` we route through
    `handle_rename_workspace`, which uses the workspace symbol index +
    import-graph reachability to rename every file in the workspace
    that imports the definition site. For local bindings (function
    parameters, indented `let`s, anonymous helpers) we walk the
    enclosing fn's brace-counted scope and only emit edits inside that
    window — preserving the LSP guarantee that param renames don't
    touch unrelated `param` identifiers in sibling fns.

    Returns either:
      * a WorkspaceEdit `{"changes": {uri: [TextEdit, ...]}}`, OR
      * `{"_rename_invalid": msg}` when the new name fails validation
        (dispatcher converts to -32602 InvalidParams), OR
      * `{"_rename_conflict": msg}` when the new name clashes with an
        existing top-level decl (-32803 Request Failed), OR
      * `{"_rename_warning": msg, "changes": {...}}` when the edit goes
        through but the user is shadowing an existing same-scope name —
        the dispatcher emits the edit + a `window/showMessage` warning, OR
      * `None` when the rename is a no-op (e.g. cursor on whitespace).
    """
    uri = params.get("textDocument", {}).get("uri", "")
    pos = params.get("position", {})
    new_name = params.get("newName") or ""
    doc = state.documents.get(uri)
    if not doc:
        return None

    # Pre-flight: the new name MUST be a valid non-keyword identifier.
    # Empty / bad-shape / keyword all return -32602 Invalid Params so the
    # editor can surface a clear error in the rename dialog.
    err = validate_new_name(new_name)
    if err is not None:
        return {"_rename_invalid": err}

    old_name = word_at(doc.text, pos.get("line", 0), pos.get("character", 0))
    if not old_name or old_name == new_name:
        return None
    # Cursor on a NOVA keyword is a no-op — prepareRename already
    # refuses those positions but defence-in-depth is cheap.
    if is_keyword(old_name):
        return None

    # Try the workspace-wide path first (top-level symbols only).
    workspace_result = handle_rename_workspace(
        state, doc, old_name, new_name
    )
    if workspace_result is not None:
        return workspace_result

    # Fall back to the scope-confined single-buffer rename. This covers
    # local bindings and the case where the symbol is unknown to the
    # import graph (e.g. completely intra-buffer use).
    return _handle_rename_local(state, doc, old_name, new_name, pos)


def handle_prepare_rename(
    state: ServerState, params: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """`textDocument/prepareRename` — validate the cursor position
    BEFORE the editor pops the rename input box.

    Returns either:
      * `{"range": <ident-range>, "placeholder": <current-name>}` when
        the cursor is on a renameable identifier, OR
      * `None` (LSP `null`) when the position is not renameable —
        cursor on a keyword, comment, string literal, whitespace, or
        an undeclared identifier the engine doesn't recognise.

    The editor uses the returned range to highlight the identifier and
    the placeholder as the prefilled text in the rename dialog.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    pos = params.get("position", {})
    doc = state.documents.get(uri)
    if not doc:
        return None
    hit = identifier_range_at(
        doc.text, pos.get("line", 0), pos.get("character", 0)
    )
    if hit is None:
        return None
    name, rng = hit
    return {"range": rng, "placeholder": name}


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


def _handle_rename_local(
    state: ServerState,
    doc: Document,
    old_name: str,
    new_name: str,
    pos: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """R32E scope-confined local-binding rename.

    For local symbols (function parameters, indented `let`s, anonymous
    helpers) we MUST not touch identically-named identifiers in sibling
    functions or other files — those are independent bindings under
    NOVA's lexical-scope rules.

    Strategy:
      * Resolve the enclosing top-level fn for the cursor position
        (brace-counted `{` / `}` walk through `enclosing_fn_range`).
      * Scan only that fn body's line range for occurrences. Skips
        comments + string literals via the shared masker.
      * Detect same-scope shadowing of `new_name` and attach a warning
        (the edit still goes through — the user can decide).

    Returns:
      * `{"changes": {uri: [edits]}}` on a clean rename, OR
      * `{"_rename_warning": msg, "changes": {...}}` when the new name
        shadows an existing same-scope binding, OR
      * `None` when no enclosing fn is found and no occurrences exist
        (cursor at file scope on a name not declared anywhere visible).
    """
    line = pos.get("line", 0)
    scope = enclosing_fn_range(doc.text, line)
    if scope is not None:
        ranges = scope_constrained_occurrences(doc.text, old_name, scope)
        if not ranges:
            return None
        warn = detect_same_scope_shadow(doc.text, new_name, scope)
        edits = [{"range": r, "newText": new_name} for r in ranges]
        wse: Dict[str, Any] = {"changes": {doc.uri: edits}}
        if warn is not None:
            return {"_rename_warning": warn, "changes": wse["changes"]}
        return wse

    # No enclosing fn — file-scope rename. Confine to the open buffer
    # only; we already know the symbol isn't a top-level workspace
    # candidate (the workspace path would have caught it).
    file_ranges = _scan_file_for_identifier(doc.text, old_name)
    if not file_ranges:
        return None
    return {
        "changes": {
            doc.uri: [
                {"range": e["range"], "newText": new_name}
                for e in file_ranges
            ]
        }
    }


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
#
# R21F moved the analysis + edit construction into the dedicated
# `nova_lsp.extract_function` module so the inline implementation
# could grow without bloating this dispatcher. The wrapper below
# (`_build_extract_action`) keeps the public name + signature stable
# for in-file callers and delegates the work to the module, passing
# the builtin function name set so the free-variable scan ignores
# `println`/`len`/etc when computing the helper's parameter list.
#
# `_find_fn_definitions` is preserved here because the sort-fns action
# (Action 3) also depends on it; the rest of the extract helpers
# (free-variable scan, locals-in-scope, etc) live in the module.


def _find_fn_definitions(text: str) -> List[Tuple[str, int, int, int]]:
    """Locate every top-level `fn name(args) { ... }` definition.

    Returns a list of `(name, start_line, body_open_line, end_line)` tuples
    where `end_line` is the line index of the closing `}` (inclusive).
    Brace counting is done from the opening `{` on the signature line.

    Also used by the sort-fns code action (Action 3) — kept in this
    file because the sort action lives here too.
    """
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


def _build_extract_action(
    doc: Document, range_: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Delegate to the R21F extract-function module.

    Threads `BUILTIN_FUNCTIONS.keys()` through so the free-variable
    scan inside the module knows which identifiers are builtins (and
    therefore not parameters of the extracted helper). The module
    returns ``None`` when the selection isn't extractable — too small,
    outside any function body, or spans a fn boundary — and the
    dispatcher transparently propagates that.
    """
    return build_extract_function_action(
        doc.uri,
        doc.text,
        range_,
        builtins=set(BUILTIN_FUNCTIONS.keys()),
    )


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
    diagnostics = context.get("diagnostics") or []

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
    if _allowed(KIND_REFACTOR_INLINE):
        # Inline-variable refactor (R25F). Detects a ``let x = expr``
        # under the cursor and offers to replace every use of ``x``
        # in the enclosing fn body with the parenthesised RHS,
        # removing the let. Refuses on reassignment, closure capture,
        # or cross-fn use; surfaces a duplicate-evaluation warning
        # in the title when the RHS looks side-effecting.
        inline = build_inline_action(doc.uri, doc.text, rng)
        if inline:
            actions.append(inline)
    if _allowed(KIND_SOURCE_ORGANIZE_IMPORTS):
        oi = _build_organize_imports_action(doc)
        if oi:
            actions.append(oi)
    if _allowed(KIND_SOURCE_ORGANIZE_FNS):
        sf = _build_sort_fns_action(doc)
        if sf:
            actions.append(sf)
    # R20D: exhaustiveness quick-fix. Driven by the diagnostics list
    # the client forwards in `context.diagnostics` — we look for
    # R17A's "non-exhaustive match on E (missing: V1, V2)" WARN and
    # emit one action per repairable match. The action is a
    # WorkspaceEdit that inserts stub arms for each missing variant
    # just above the catch-all (or the closing `}` when no catch-all).
    if _allowed(KIND_QUICKFIX) and diagnostics:
        # Warm the workspace symbol index so a cross-file enum decl
        # (declared in shapes.nova, matched in main.nova) is found
        # even when no `import "..."` exists yet — same warming dance
        # the call/type hierarchy handlers use. Cheap once warm.
        if state.root_path:
            state.workspace_symbols.index_workspace_root(state.root_path)
        for d in state.documents.values():
            _refresh_workspace_symbols_for_doc(state, d)
        start_path = uri_to_path(doc.uri)
        if start_path:
            start_path = os.path.abspath(start_path)
        ex_actions = build_exhaustiveness_code_actions(
            uri=doc.uri,
            doc_text=doc.text,
            diagnostics=diagnostics,
            file_cache=state.file_cache,
            workspace_index=state.workspace_symbols,
            text_overrides=_text_overrides(state),
            start_path=start_path,
        )
        actions.extend(ex_actions)

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
# Inlay hints — `textDocument/inlayHint`.
#
# Renders parameter-name ghost text inline at function call sites so a
# reader can tell which positional argument maps to which declared
# parameter without jumping to the definition. Also emits literal-RHS
# type hints on `let x = <literal>` bindings.
#
# Callee resolution shares R5F's `find_definition` over the import
# graph + R8C's workspace symbol index for sibling files outside the
# graph; builtins (no source location) yield no hint. See
# `inlay_hints.py` for the argument-position parsing details.
# ---------------------------------------------------------------------------


def handle_inlay_hints(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return InlayHint[] for the viewport `range` in `params.textDocument`.

    Warms the workspace symbol index (so cross-file callee resolution
    sees sibling fns outside the import graph) before delegating to
    `inlay_hints.compute_inlay_hints`.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    if not doc:
        return []
    rng = params.get("range") or None
    # Warm the workspace index so callee resolution can fall back to
    # sibling files. Cheap once warm (lazy crawl is gated by
    # `_crawled_roots`).
    if state.root_path:
        state.workspace_symbols.index_workspace_root(state.root_path)
    for d in state.documents.values():
        _refresh_workspace_symbols_for_doc(state, d)
    return compute_inlay_hints(
        uri=doc.uri,
        range_=rng,
        doc_text=doc.text,
        file_cache=state.file_cache,
        workspace_index=state.workspace_symbols,
        text_overrides=_text_overrides(state),
    )


# ---------------------------------------------------------------------------
# Code lens — `textDocument/codeLens` + `codeLens/resolve`.
#
# Renders a clickable summary line above each top-level declaration —
# "N references" on fn / let / const, "N variants used" on enum, with
# an optional "/ tested" suffix when a tests/test_*.nova mentions the
# decl. Reference counts share R8C's workspace symbol index + R9C's
# reference-finding shape; see `code_lens.py` for the per-decl
# breakdown.
# ---------------------------------------------------------------------------


def _warm_workspace_for_code_lens(state: ServerState) -> List[str]:
    """Make sure the workspace symbol index has seen the project root
    and every open buffer; return the union of open-doc paths +
    transitive import closures so cross-file reference counts include
    files outside the indexed workspace root.

    Mirrors `_warm_workspace_for_call_hierarchy` — same warming dance,
    different downstream consumer.
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


def handle_code_lens(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return CodeLens[] for the document in `params.textDocument`.

    Warms the workspace index + import closure (so a cross-file
    caller counted by the lens is actually visible) before delegating
    to `code_lens.compute_code_lenses`. Returns an empty list when
    the document isn't open in the server — the client should send
    `didOpen` before requesting lenses.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    if not doc:
        return []
    extra_paths = _warm_workspace_for_code_lens(state)
    return compute_code_lenses(
        uri=doc.uri,
        doc_text=doc.text,
        file_cache=state.file_cache,
        workspace_index=state.workspace_symbols,
        extra_paths=extra_paths,
        text_overrides=_text_overrides(state),
        workspace_root=state.root_path,
    )


def handle_code_lens_resolve(
    state: ServerState, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Resolve a single CodeLens.

    Pass-through in the current implementation (we eagerly populate
    `command` in `handle_code_lens`). Declared in `server_capabilities`
    so the client wire shape stays compatible with future rounds that
    might shift expensive resolution out of the initial response.
    """
    return resolve_code_lens(params or {})


# ---------------------------------------------------------------------------
# Document links — `textDocument/documentLink`.
#
# Turns every `import "path/to/file.nova"` statement in the open buffer
# into a clickable hyperlink. The link's target URI is the resolved
# absolute path of the import (relative paths are joined against the
# current file's directory). Dead links — paths that don't exist on
# disk — are STILL emitted; the editor surfaces them via its own
# dead-link UI (red squiggle on click) so the user can spot typos
# without the LSP having to stat every import on every keystroke.
# Single-file analysis — no workspace warm-up needed.
# ---------------------------------------------------------------------------


def handle_document_link(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return DocumentLink[] for the document under `uri`.

    Per LSP spec the response is `DocumentLink[] | null`; we return an
    empty list (which the client treats identically to null) when the
    document isn't open in the server. Each link covers the path text
    BETWEEN the quotes (not the quote characters themselves) so the
    editor's underline lines up under the visible path.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    if not doc:
        return []
    return compute_document_links(uri=doc.uri, doc_text=doc.text)


# ---------------------------------------------------------------------------
# Type hierarchy — `textDocument/prepareTypeHierarchy`,
# `typeHierarchy/supertypes`, `typeHierarchy/subtypes`.
#
# The 15th LSP capability. Renders sub/supertype navigation in the
# editor: an `enum` declaration expands to its variants, a `type T = U`
# alias surfaces `U` as a supertype and every other `type X = T` alias
# as a subtype. NOVA's type system has no formal inheritance, so the
# implementation reduces to the variants-of-enum and aliases-of-type
# rules. See `type_hierarchy.py` for the per-decl parsing details.
# ---------------------------------------------------------------------------


def _warm_workspace_for_type_hierarchy(state: ServerState) -> List[str]:
    """Make sure the workspace symbol index has seen the project root
    and every open buffer, returning the union of open-doc paths plus
    transitive import closures so type-hierarchy scans don't miss
    files outside the indexed workspace root.

    Mirrors `_warm_workspace_for_call_hierarchy` / `_for_code_lens` —
    same warming dance, different downstream consumer.
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


def handle_prepare_type_hierarchy(
    state: ServerState, params: Dict[str, Any]
) -> Optional[List[Dict[str, Any]]]:
    """Resolve the cursor's symbol into a TypeHierarchyItem[].

    Returns either:
      * `None` -> the cursor isn't on a recognised type / enum
        identifier (LSP clients render this as "no type hierarchy
        available").
      * `[TypeHierarchyItem]` -> the single-element list the editor
        then passes to `supertypes` / `subtypes`.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    pos = params.get("position", {}) or {}
    doc = state.documents.get(uri)
    if not doc:
        return None
    # Warm so cross-file resolution sees siblings outside the import
    # graph.
    _warm_workspace_for_type_hierarchy(state)
    return prepare_type_hierarchy(
        uri=uri,
        line=pos.get("line", 0),
        character=pos.get("character", 0),
        doc_text=doc.text,
        file_cache=state.file_cache,
        workspace_index=state.workspace_symbols,
        text_overrides=_text_overrides(state),
    )


def handle_type_supertypes(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return every supertype of `params.item`.

    LSP shape: `{"item": TypeHierarchyItem}` in, `TypeHierarchyItem[]`
    out. Empty list when the item has no supertype (enums and structs
    don't inherit; root type aliases that already point to builtins
    surface those builtins via synthetic placeholder items).
    """
    item = params.get("item") or {}
    if not item.get("name"):
        return []
    _warm_workspace_for_type_hierarchy(state)
    return supertypes(
        item,
        state.file_cache,
        workspace_index=state.workspace_symbols,
        text_overrides=_text_overrides(state),
    )


def handle_type_subtypes(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return every subtype of `params.item`.

    LSP shape: `{"item": TypeHierarchyItem}` in, `TypeHierarchyItem[]`
    out. For an enum, the result is the list of declared variants; for
    a type alias, the result is every other alias in the workspace
    that names this alias on its RHS. Structs and variant members are
    leaves (empty list).
    """
    item = params.get("item") or {}
    if not item.get("name"):
        return []
    extra_paths = _warm_workspace_for_type_hierarchy(state)
    return subtypes(
        item,
        state.file_cache,
        state.workspace_symbols,
        extra_paths=extra_paths,
        text_overrides=_text_overrides(state),
    )


# ---------------------------------------------------------------------------
# Folding ranges — `textDocument/foldingRange`.
#
# The 16th LSP capability. Returns FoldingRange[] for collapsible
# blocks: fn bodies, match / if / else expressions, enum + struct
# bodies, contiguous `///` doc-comment blocks, and contiguous import
# blocks. Folding is purely syntactic and single-file — no workspace
# warm-up needed.
# ---------------------------------------------------------------------------


def handle_folding_range(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return FoldingRange[] for the document under `uri`.

    Per LSP spec the response is `FoldingRange[] | null`; we return an
    empty list (which the client treats identically to null) when the
    document isn't open in the server. Folding ranges are purely
    syntactic so there's no workspace index warm-up — the analysis runs
    over the current buffer text in isolation.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    if not doc:
        return []
    return compute_folding_ranges(
        uri=doc.uri,
        doc_text=doc.text,
        file_cache=state.file_cache,
    )


# ---------------------------------------------------------------------------
# Document symbols — `textDocument/documentSymbol`.
#
# The 17th LSP capability. Returns a hierarchical DocumentSymbol[] tree
# driving the editor's outline panel + Cmd+Shift+O quick-pick. Top-level
# fn / let / const / type / enum / struct each surface as one symbol;
# enum variants and struct fields nest as children of their parent
# declaration. Single-file analysis — no workspace warm-up needed.
# ---------------------------------------------------------------------------


def handle_document_symbol(
    state: ServerState, params: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return DocumentSymbol[] for the document under `uri`.

    Per LSP spec the response is `DocumentSymbol[] | SymbolInformation[] | null`;
    we return the modern hierarchical `DocumentSymbol[]` shape so the
    editor renders parent/child containment (variants under their
    enum, fields under their struct). Returns an empty list when the
    document isn't open in the server.
    """
    uri = params.get("textDocument", {}).get("uri", "")
    doc = state.documents.get(uri)
    if not doc:
        return []
    return compute_document_symbols(
        uri=doc.uri,
        doc_text=doc.text,
        file_cache=state.file_cache,
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
        "renameProvider": {"prepareProvider": True},
        "referencesProvider": True,
        "codeActionProvider": {
            "codeActionKinds": [
                KIND_REFACTOR_EXTRACT,
                KIND_REFACTOR_INLINE,
                KIND_SOURCE_ORGANIZE_IMPORTS,
                KIND_SOURCE_ORGANIZE_FNS,
                KIND_QUICKFIX,
            ],
        },
        "workspaceSymbolProvider": {"resolveProvider": False},
        "semanticTokensProvider": {
            "legend": semantic_tokens_legend(),
            "range": True,
            "full": True,
        },
        "callHierarchyProvider": True,
        "inlayHintProvider": {"resolveProvider": False},
        "codeLensProvider": {"resolveProvider": True},
        # R33D: clickable `import "..."` paths. No resolveProvider — every
        # link is fully populated (`range` + `target`) on the initial
        # request because resolving an import path is cheap (one
        # `os.path.join` + `os.path.abspath`); no need to defer.
        "documentLinkProvider": {},
        "typeHierarchyProvider": True,
        "foldingRangeProvider": True,
        "documentSymbolProvider": True,
        "diagnosticProvider": {
            "interFileDependencies": False,
            # workspace/diagnostic: returns one WorkspaceDocumentDiagnosticReport
            # per indexed file, driven by `compute_workspace_diagnostics`.
            # Incremental support via `previousResultIds` -- files whose
            # content hash matches the previousResultId return
            # `kind: "unchanged"`, the rest return `kind: "full"` with a
            # fresh items + resultId payload.
            "workspaceDiagnostics": True,
        },
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
        if isinstance(result, dict) and "_rename_invalid" in result:
            # R32E: new name failed validation (bad shape / keyword). LSP
            # spec uses -32602 Invalid Params for malformed requests, and
            # VS Code surfaces the message as an inline error in the
            # rename input box.
            write_message(
                out_stream,
                make_error(req_id, -32602, result["_rename_invalid"]),
            )
            return True
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
        if isinstance(result, dict) and "_rename_warning" in result:
            # R32E: same-scope shadow detected. Per LSP convention we
            # still emit the WorkspaceEdit but precede it with a
            # `window/showMessage` warning so the editor surfaces the
            # caveat. The edit itself is returned in the response.
            warn = result.pop("_rename_warning")
            write_message(
                out_stream,
                {
                    "jsonrpc": "2.0",
                    "method": "window/showMessage",
                    "params": {"type": 2, "message": warn},
                },
            )
            write_message(out_stream, make_response(req_id, result))
            return True
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/prepareRename":
        # R32E: validate the cursor position before the editor pops the
        # rename dialog. Returns `{range, placeholder}` for renameable
        # identifiers, `null` otherwise.
        result = handle_prepare_rename(state, params)
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
    if method == "textDocument/inlayHint":
        result = handle_inlay_hints(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/codeLens":
        result = handle_code_lens(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "codeLens/resolve":
        result = handle_code_lens_resolve(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/documentLink":
        result = handle_document_link(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/prepareTypeHierarchy":
        result = handle_prepare_type_hierarchy(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "typeHierarchy/supertypes":
        result = handle_type_supertypes(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "typeHierarchy/subtypes":
        result = handle_type_subtypes(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/foldingRange":
        result = handle_folding_range(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "textDocument/documentSymbol":
        result = handle_document_symbol(state, params)
        write_message(out_stream, make_response(req_id, result))
        return True
    if method == "workspace/diagnostic":
        result = handle_workspace_diagnostic(state, params)
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
