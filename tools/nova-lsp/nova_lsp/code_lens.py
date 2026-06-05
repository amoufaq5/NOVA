"""Code lens (`textDocument/codeLens` + `codeLens/resolve`).

Code lenses are LSP annotations rendered **above** a declaration —
clickable summary text such as "3 references" or "0 readers". They
are less invasive than inlay hints (which sit inline at the cursor's
own column) because they live on a synthetic line of their own and
don't shift any source position. VS Code calls them "code lenses",
JetBrains shows them as "code vision", and most other LSP clients
support the same rendering.

For NOVA we surface one lens per top-level declaration:

  * ``fn name(args)``      -> ``"N references"`` (call sites + plain
                              identifier references workspace-wide)
  * ``enum Name { ... }``  -> ``"V variants used"`` (number of distinct
                              variant constructors / patterns referenced
                              via ``Name::Variant`` or ``Name.Variant``)
  * ``let NAME = ...``     -> ``"N readers"`` for an ALL-CAPS constant
                              or a mixed-case top-level binding.

If a corresponding test file exists in ``tests/`` that exercises the
declaration, we append a ``" / tested"`` marker to the lens title.
Test detection is a deliberately light grep — we look for the
declaration's name inside any ``tests/test_*.nova`` file.

LSP wire shape per the spec
(`Code Lens Request <https://microsoft.github.io/language-server-protocol/specification/#textDocument_codeLens>`_):

    interface CodeLens {
        range: Range;
        command?: Command;
        data?: any;        // server-private; passed back on resolve.
    }

The server returns lenses with their ``command`` fully populated so a
single round-trip is sufficient (``resolveProvider`` declared as
``true`` for forward compatibility — a future capability that needs
expensive resolution can re-use the same handler). ``codeLens/resolve``
is a no-op pass-through in the current implementation: it just hands
back whatever the client sends, retaining the original ``command`` and
``data`` payload.

Resolution path:

  1. Parse the file via the same regexes used by R8C's
     :func:`workspace_symbols.scan_symbols` — top-level ``fn``,
     ``let`` / ``const``, plus ``enum`` (a NOVA-specific decl kind R8C
     doesn't track because ``workspace/symbol`` doesn't surface it).
  2. For each declaration name, count workspace references using a
     scheme parallel to R9C's
     :func:`rename_workspace.find_references_in_workspace`:
       * union the workspace index file list with the open buffer +
         its transitive import closure;
       * mask comments / string literals so a name inside ``// foo``
         isn't counted;
       * subtract the declaration line itself so the count is
         "external references" rather than "name occurrences";
       * for enums, count *unique* variant names referenced rather
         than every variant occurrence — that's the metric users care
         about (how many constructors of this enum are in use).
  3. Emit a `CodeLens` per declaration with ``command.command`` set to
     a stable identifier the editor binds to (LSP's
     ``editor.action.showReferences`` is the de-facto standard);
     ``command.arguments`` carries the declaration's URI + position so
     the editor can drive a references search without re-querying.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from nova_lsp.imports import FileCache, walk_imports
from nova_lsp.workspace_symbols import WorkspaceSymbolIndex


# ---------------------------------------------------------------------------
# Declaration patterns.
#
# These mirror `workspace_symbols._FN_DEF_RE` / `_LET_DEF_RE` but
# additionally recognise `enum`, `const`, and capture the declaration's
# name token columns so the lens can target the editor's "show
# references" action with the right position.
# ---------------------------------------------------------------------------

_FN_DEF_RE = re.compile(r"^fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_LET_DEF_RE = re.compile(r"^let\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")
_CONST_DEF_RE = re.compile(r"^const\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")
_ENUM_DEF_RE = re.compile(r"^enum\s+([A-Za-z_][A-Za-z0-9_]*)\b")

_ALL_CAPS_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


# Declaration "kinds" we surface in the lens title. Kept as plain
# strings so the test harness can assert on the bucket without coupling
# to LSP's `SymbolKind` enum.
KIND_FN = "fn"
KIND_LET = "let"
KIND_CONST = "const"
KIND_ENUM = "enum"


# ---------------------------------------------------------------------------
# Comment / string masking — same idea as the rename_workspace,
# call_hierarchy, and inlay_hints helpers. Replaces masked regions with
# spaces so column offsets are preserved but identifier-matching never
# lands inside a quoted literal or a `//` / `#` comment.
# ---------------------------------------------------------------------------


def _mask_comments_and_strings(line: str) -> str:
    out: List[str] = []
    i = 0
    n = len(line)
    in_string = False
    while i < n:
        ch = line[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(" ")
                out.append(" ")
                i += 2
                continue
            if ch == '"':
                in_string = False
                out.append('"')
                i += 1
                continue
            out.append(" ")
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append('"')
            i += 1
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            out.extend(" " * (n - i))
            break
        if ch == "#":
            out.extend(" " * (n - i))
            break
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Declaration extraction.
# ---------------------------------------------------------------------------


@dataclass
class Declaration:
    """One top-level declaration parsed out of a NOVA source file.

    `line` / `name_char_start` / `name_char_end` describe the column
    span of the declaration's name token on ``line`` (zero-based).
    The lens itself is rendered ABOVE this line (per LSP convention,
    the lens range is a zero-width span at ``(line, 0)`` — the client
    inserts the synthetic line for us). `kind` is one of
    ``KIND_FN`` / ``KIND_LET`` / ``KIND_CONST`` / ``KIND_ENUM``.
    """
    name: str
    kind: str
    line: int
    name_char_start: int
    name_char_end: int


def scan_declarations(text: str) -> List[Declaration]:
    """Return every top-level declaration in `text`, source order.

    "Top-level" means column zero — anything indented is a local
    binding (function parameter, inner ``let``) and gets no lens.
    The classifier mirrors `workspace_symbols.scan_symbols` so the
    lens count and the workspace symbol picker stay consistent.
    """
    out: List[Declaration] = []
    for line_no, line in enumerate(text.splitlines()):
        m = _FN_DEF_RE.match(line)
        if m:
            out.append(Declaration(
                name=m.group(1),
                kind=KIND_FN,
                line=line_no,
                name_char_start=m.start(1),
                name_char_end=m.end(1),
            ))
            continue
        m = _CONST_DEF_RE.match(line)
        if m:
            out.append(Declaration(
                name=m.group(1),
                kind=KIND_CONST,
                line=line_no,
                name_char_start=m.start(1),
                name_char_end=m.end(1),
            ))
            continue
        m = _LET_DEF_RE.match(line)
        if m:
            out.append(Declaration(
                name=m.group(1),
                kind=KIND_LET,
                line=line_no,
                name_char_start=m.start(1),
                name_char_end=m.end(1),
            ))
            continue
        m = _ENUM_DEF_RE.match(line)
        if m:
            out.append(Declaration(
                name=m.group(1),
                kind=KIND_ENUM,
                line=line_no,
                name_char_start=m.start(1),
                name_char_end=m.end(1),
            ))
            continue
    return out


# ---------------------------------------------------------------------------
# Reference counting.
# ---------------------------------------------------------------------------


def _ident_regex(name: str) -> "re.Pattern[str]":
    """Whole-word identifier regex; word boundaries on both sides so
    ``foo`` doesn't match inside ``foobar`` or ``myfoo``."""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")


def _enum_variant_regex(enum_name: str) -> "re.Pattern[str]":
    """Match ``Name::Variant`` or ``Name.Variant`` for `enum_name`.

    NOVA's R17A grammar uses ``::`` for sum-type constructors, but the
    pre-R17A C-style ``enum`` syntax (used by R16B for plain
    discriminants) accesses variants via ``.`` — we accept both so the
    lens count is meaningful across the entire codebase.
    """
    return re.compile(
        r"(?<![A-Za-z0-9_])"
        + re.escape(enum_name)
        + r"(?:::|\.)([A-Za-z_][A-Za-z0-9_]*)"
    )


def _count_references_in_text(
    name: str,
    text: str,
    *,
    skip_line: Optional[int] = None,
) -> int:
    """Count every `\\bname\\b` occurrence in `text`, excluding
    matches that fall inside strings or comments and (optionally)
    the declaration line itself.

    The declaration line is skipped because the user typically wants
    "places that USE this name elsewhere" — counting the decl as a
    self-reference would inflate the lens by 1 and read confusingly
    (e.g. an unused ``fn foo`` would show "1 reference").
    """
    pattern = _ident_regex(name)
    count = 0
    for line_no, line in enumerate(text.splitlines()):
        if skip_line is not None and line_no == skip_line:
            continue
        cleaned = _mask_comments_and_strings(line)
        for _ in pattern.finditer(cleaned):
            count += 1
    return count


def _count_unique_variants_in_text(
    enum_name: str,
    text: str,
    *,
    skip_line: Optional[int] = None,
    declared_variants: Optional[Set[str]] = None,
) -> int:
    """Return the number of DISTINCT variant names referenced as
    ``enum_name::Variant`` or ``enum_name.Variant`` in `text`.

    The skip-line semantics mirror `_count_references_in_text` so the
    declaration itself doesn't self-count. When `declared_variants`
    is supplied we restrict the count to variants actually declared on
    the enum — otherwise a stray ``Name.foo`` (where ``foo`` is unrelated)
    would inflate the count.
    """
    pattern = _enum_variant_regex(enum_name)
    seen: Set[str] = set()
    for line_no, line in enumerate(text.splitlines()):
        if skip_line is not None and line_no == skip_line:
            continue
        cleaned = _mask_comments_and_strings(line)
        for m in pattern.finditer(cleaned):
            variant = m.group(1)
            if declared_variants is not None and variant not in declared_variants:
                continue
            seen.add(variant)
    return len(seen)


def _enum_declared_variants(text: str, enum_name: str) -> Set[str]:
    """Parse ``enum Name { ... }`` out of `text` and return the set of
    variant identifiers it declares.

    Brace-counted to handle multi-line bodies; ignores ``,`` and
    payload parentheses so ``Some(int)`` -> ``Some``. Used by
    `_count_unique_variants_in_text` to filter out unrelated
    ``Name.something`` accesses (e.g. instance methods if NOVA ever
    grows them).
    """
    lines = text.splitlines()
    decl_pat = re.compile(r"^enum\s+" + re.escape(enum_name) + r"\b")
    in_body = False
    brace_depth = 0
    variants: Set[str] = set()
    ident_pat = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")
    for line in lines:
        if not in_body:
            if decl_pat.match(line):
                if "{" in line:
                    in_body = True
                    brace_depth = line.count("{") - line.count("}")
                    # capture variants after the `{` on the same line
                    after = line.split("{", 1)[1]
                    for m in ident_pat.finditer(_mask_comments_and_strings(after)):
                        variants.add(m.group(1))
                continue
            continue
        cleaned = _mask_comments_and_strings(line)
        brace_depth += cleaned.count("{") - cleaned.count("}")
        for m in ident_pat.finditer(cleaned):
            variants.add(m.group(1))
        if brace_depth <= 0:
            break
    return variants


# ---------------------------------------------------------------------------
# Workspace candidate file enumeration — same shape as
# `rename_workspace.find_references_in_workspace`.
# ---------------------------------------------------------------------------


def _candidate_paths(
    def_path: str,
    file_cache: FileCache,
    workspace_index: WorkspaceSymbolIndex,
    *,
    extra_paths: Optional[List[str]] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Union of every absolute path we should scan for references:
    the workspace symbol index, the explicitly-passed `extra_paths`
    (open buffers + their transitive imports), and the definition
    file itself. Returned in deterministic sort order.
    """
    overrides = text_overrides or {}
    paths: Set[str] = set()
    # noinspection PyProtectedMember
    for p in workspace_index._by_file.keys():  # noqa: SLF001 (intentional)
        paths.add(os.path.abspath(p))
    if extra_paths:
        for p in extra_paths:
            paths.add(os.path.abspath(p))
    paths.add(os.path.abspath(def_path))
    # Pull in the transitive import closure rooted at def_path so a
    # cross-file caller that isn't in the workspace index (e.g. only
    # reachable via imports) is still counted. Cheap thanks to the
    # mtime-keyed FileCache.
    for entry in walk_imports(
        os.path.abspath(def_path), file_cache, text_overrides=overrides
    ):
        paths.add(entry.path)
    return sorted(paths)


def _read_text(
    path: str,
    file_cache: FileCache,
    text_overrides: Dict[str, str],
) -> Optional[str]:
    """Return the live buffer text for `path` if open, else the cached
    on-disk content. None when the file can't be read at all.
    """
    override = text_overrides.get(os.path.abspath(path))
    if override is not None:
        return override
    entry = file_cache.get(path)
    if entry is None:
        return None
    return entry.text


def count_workspace_references(
    name: str,
    def_path: str,
    def_line: int,
    file_cache: FileCache,
    workspace_index: WorkspaceSymbolIndex,
    *,
    extra_paths: Optional[List[str]] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> int:
    """Count every `\\bname\\b` reference across the workspace,
    excluding the declaration site itself.

    Mirrors `rename_workspace.find_references_in_workspace` in shape
    but returns an integer rather than per-file ranges — the lens
    only renders a count.
    """
    overrides = text_overrides or {}
    total = 0
    for path in _candidate_paths(
        def_path,
        file_cache,
        workspace_index,
        extra_paths=extra_paths,
        text_overrides=overrides,
    ):
        text = _read_text(path, file_cache, overrides)
        if text is None:
            continue
        skip = def_line if os.path.abspath(path) == os.path.abspath(def_path) else None
        total += _count_references_in_text(name, text, skip_line=skip)
    return total


def count_workspace_enum_variants_used(
    enum_name: str,
    def_path: str,
    def_line: int,
    file_cache: FileCache,
    workspace_index: WorkspaceSymbolIndex,
    *,
    extra_paths: Optional[List[str]] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> int:
    """Workspace-wide count of distinct variants of `enum_name` used
    in ``Name::Variant`` or ``Name.Variant`` form.

    Restricted to variants declared on the enum so an unrelated
    ``Name.something`` doesn't inflate the count.
    """
    overrides = text_overrides or {}
    def_text = _read_text(def_path, file_cache, overrides)
    declared: Set[str] = set()
    if def_text is not None:
        declared = _enum_declared_variants(def_text, enum_name)
    seen: Set[str] = set()
    for path in _candidate_paths(
        def_path,
        file_cache,
        workspace_index,
        extra_paths=extra_paths,
        text_overrides=overrides,
    ):
        text = _read_text(path, file_cache, overrides)
        if text is None:
            continue
        skip = def_line if os.path.abspath(path) == os.path.abspath(def_path) else None
        pattern = _enum_variant_regex(enum_name)
        for line_no, line in enumerate(text.splitlines()):
            if skip is not None and line_no == skip:
                continue
            cleaned = _mask_comments_and_strings(line)
            for m in pattern.finditer(cleaned):
                variant = m.group(1)
                if declared and variant not in declared:
                    continue
                seen.add(variant)
    return len(seen)


# ---------------------------------------------------------------------------
# Tested-marker — `tests/test_*.nova` mentioning the declaration name.
# ---------------------------------------------------------------------------


def has_corresponding_test(
    name: str,
    workspace_root: Optional[str],
) -> bool:
    """Return True when a ``tests/test_*.nova`` file under
    `workspace_root` mentions `name` (whole-word, comments/strings
    masked out so an unrelated ``// foo`` doesn't count).

    Returns False when `workspace_root` is None or no ``tests`` dir
    exists. Walks only one directory deep — sufficient for NOVA's
    flat ``tests/`` layout and keeps the lens cheap to compute.
    """
    if not workspace_root:
        return False
    tests_dir = os.path.join(workspace_root, "tests")
    if not os.path.isdir(tests_dir):
        return False
    pattern = _ident_regex(name)
    try:
        for fname in os.listdir(tests_dir):
            if not fname.startswith("test_") or not fname.endswith(".nova"):
                continue
            path = os.path.join(tests_dir, fname)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            for line in text.splitlines():
                cleaned = _mask_comments_and_strings(line)
                if pattern.search(cleaned):
                    return True
    except OSError:
        return False
    return False


# ---------------------------------------------------------------------------
# CodeLens construction.
# ---------------------------------------------------------------------------


def _lens_title(decl: Declaration, count: int, tested: bool) -> str:
    """Render the lens label, e.g. ``"3 references"`` /
    ``"0 readers"`` / ``"2 variants used / tested"``.

    Pluralisation uses the standard English ``s`` suffix — count == 1
    yields ``"1 reference"``, not ``"1 references"``, matching the
    pattern users see in rust-analyzer / TypeScript / pyright.
    """
    if decl.kind == KIND_FN:
        unit = "reference" if count == 1 else "references"
        title = f"{count} {unit}"
    elif decl.kind == KIND_ENUM:
        unit = "variant used" if count == 1 else "variants used"
        title = f"{count} {unit}"
    else:
        # let / const both render as "N readers".
        unit = "reader" if count == 1 else "readers"
        title = f"{count} {unit}"
    if tested:
        title = f"{title} / tested"
    return title


def build_code_lens(
    decl: Declaration,
    uri: str,
    count: int,
    tested: bool,
) -> Dict[str, Any]:
    """Construct one LSP CodeLens payload for `decl` in `uri`.

    The lens occupies a zero-width range at the start of the
    declaration line (line, col 0 -> line, col 0). The editor draws
    the lens as a synthetic line above the declaration.

    ``command.command`` is ``editor.action.showReferences`` — the
    well-known VS Code action the editor binds to "open the
    references panel for this position". ``command.arguments`` carries
    ``[uri, position, [Location, ...]]`` per the action's contract;
    we ship an empty location list because the client re-resolves
    references on click via the standard ``textDocument/references``
    request anyway.

    ``data`` mirrors the lens body so a follow-up ``codeLens/resolve``
    call can reconstruct the title without re-counting (currently a
    no-op pass-through — see module docstring).
    """
    title = _lens_title(decl, count, tested)
    return {
        "range": {
            "start": {"line": decl.line, "character": 0},
            "end": {"line": decl.line, "character": 0},
        },
        "command": {
            "title": title,
            "command": "editor.action.showReferences",
            "arguments": [
                uri,
                {"line": decl.line, "character": decl.name_char_start},
                [],
            ],
        },
        "data": {
            "uri": uri,
            "name": decl.name,
            "kind": decl.kind,
            "count": count,
            "tested": tested,
            "line": decl.line,
        },
    }


# ---------------------------------------------------------------------------
# Top-level entry points.
# ---------------------------------------------------------------------------


def _uri_to_abs(uri: str) -> Optional[str]:
    """Tiny mirror of ``server.uri_to_path -> abspath``."""
    if not uri.startswith("file://"):
        return None
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    return os.path.abspath(unquote(parsed.path))


def compute_code_lenses(
    uri: str,
    doc_text: str,
    file_cache: FileCache,
    workspace_index: WorkspaceSymbolIndex,
    *,
    extra_paths: Optional[List[str]] = None,
    text_overrides: Optional[Dict[str, str]] = None,
    workspace_root: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Compute the CodeLens[] for the document at `uri`.

    Parameters:
      `uri`              -- doc URI (derive abs path for self-reference filtering).
      `doc_text`         -- current buffer text (authoritative over disk).
      `file_cache`       -- R5F's mtime-keyed cache, reused for cross-file scans.
      `workspace_index`  -- R8C's symbol index, source of the candidate file set.
      `extra_paths`      -- open-buffer paths + their import closure, unioned in.
      `text_overrides`   -- open-buffer path -> text map, used for both reading
                            and skipping disk reloads.
      `workspace_root`   -- absolute path used by the ``tested`` lookup; pass
                            the server's ``state.root_path`` here.

    Returns a list of CodeLens[] entries (LSP spec shape), one per
    top-level declaration in `doc_text`. Order is source order so the
    editor renders the lenses in the same visual sequence as the
    declarations themselves.
    """
    def_path = _uri_to_abs(uri)
    if def_path is None:
        return []
    overrides = dict(text_overrides or {})
    # Make sure the buffer's own text wins over any stale on-disk copy
    # when we count its self-references (and indirectly when other
    # files in the workspace import it).
    overrides[def_path] = doc_text
    decls = scan_declarations(doc_text)
    out: List[Dict[str, Any]] = []
    for decl in decls:
        if decl.kind == KIND_ENUM:
            count = count_workspace_enum_variants_used(
                decl.name,
                def_path,
                decl.line,
                file_cache,
                workspace_index,
                extra_paths=extra_paths,
                text_overrides=overrides,
            )
        else:
            count = count_workspace_references(
                decl.name,
                def_path,
                decl.line,
                file_cache,
                workspace_index,
                extra_paths=extra_paths,
                text_overrides=overrides,
            )
        tested = has_corresponding_test(decl.name, workspace_root)
        out.append(build_code_lens(decl, uri, count, tested))
    # R33D: append Run / Debug test lenses for top-level `fn test_*` decls.
    # These are stacked ABOVE the reference-count lens so the user reads
    # "▶ Run | ⏷ Debug | N references" from top to bottom — the test
    # actions come first because clicking is what users do most often on
    # a test fn (vs reading its callers).
    out.extend(compute_test_code_lenses(uri, doc_text))
    return out


def resolve_code_lens(lens: Dict[str, Any]) -> Dict[str, Any]:
    """Lazy-resolution pass-through.

    The current implementation eagerly populates ``command`` in
    `compute_code_lenses`, so this handler simply returns its input.
    Declared ``resolveProvider: true`` in `server_capabilities` keeps
    the wire shape forward-compatible: a later round can shift the
    expensive work into this function (e.g. counting cross-repo
    references via an external grep) without changing the protocol.
    """
    if not isinstance(lens, dict):
        return lens
    # If the lens arrived without a `command` (e.g. an older client
    # sent only the raw range + data), synthesise one from `data` so
    # the editor still has something clickable.
    if "command" not in lens or not lens.get("command"):
        data = lens.get("data") or {}
        if isinstance(data, dict):
            name = data.get("name")
            kind = data.get("kind")
            count = data.get("count", 0)
            tested = bool(data.get("tested", False))
            line = data.get("line", 0)
            if name and kind:
                synth_decl = Declaration(
                    name=name,
                    kind=kind,
                    line=line,
                    name_char_start=0,
                    name_char_end=len(name),
                )
                lens["command"] = build_code_lens(
                    synth_decl,
                    data.get("uri", ""),
                    int(count),
                    tested,
                )["command"]
    return lens


# ---------------------------------------------------------------------------
# R33D — Run / Debug test lenses for top-level `fn test_*` declarations.
#
# NOVA's test convention (per ``tests/test_path.nova``, ``test_runtime.nova``,
# etc) is that every top-level fn whose name starts with ``test_`` is a
# self-contained test case the test harness invokes from the file's
# ``main()`` driver. R33D surfaces two clickable lenses above each such
# decl:
#
#   * ``▶ Run``    -> client command ``nova-lsp.runTest`` with the test
#                     file path + fn name, so the editor can shell out to
#                     ``bin/nova <file>`` (or a more targeted harness).
#   * ``⏷ Debug``  -> client command ``nova-lsp.debugTest`` with the same
#                     payload so the editor can launch the test under
#                     nova-dap (the DAP server lives at tools/nova-dap).
#
# The lenses are emitted at the FN'S declaration line (line 0-indexed,
# column 0). The editor draws them as a synthetic line ABOVE the source
# line — both lenses sit on the same synthetic row separated by the
# editor's lens separator (typically ``|``).
#
# Why not a separate code_lens module? The LSP wire only allows one
# ``textDocument/codeLens`` response per file; multiple servers can't
# easily compose their lens lists. Sharing the dispatcher path with the
# existing reference-count lens (R10-era) keeps the wire shape clean and
# lets the user read both annotations on the same hover.
# ---------------------------------------------------------------------------


# `nova-lsp.runTest` / `nova-lsp.debugTest` — the client command IDs the
# editor binds to. Naming follows the LSP de-facto convention of
# `<server-name>.<verb>` so a generic VS Code keybinding can route the
# action via a single dispatcher (`commands.registerCommand`).
TEST_RUN_COMMAND = "nova-lsp.runTest"
TEST_DEBUG_COMMAND = "nova-lsp.debugTest"

TEST_RUN_TITLE = "▶ Run"        # ▶ Run
TEST_DEBUG_TITLE = "⏷ Debug"    # ⏷ Debug


# Top-level `fn test_*` matcher. Anchored at column zero so an indented
# (nested) `fn test_x` inside another function body is NOT picked up —
# nested fns in NOVA aren't directly invokable as tests by the harness,
# and the test discovery scan in tests/run_tests.sh only finds top-level
# fns anyway, so matching the harness's actual behaviour is the right
# call.
_TEST_FN_DEF_RE = re.compile(
    r"^fn\s+(test_[A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)"
)


@dataclass
class TestFunction:
    """One top-level ``fn test_*(...)`` declaration parsed out of a NOVA file.

    ``name`` is the full fn name including the ``test_`` prefix (so a
    file with ``fn test_join()`` yields ``TestFunction(name="test_join", ...)``).
    ``line`` is the zero-based source line of the declaration; the Run /
    Debug lenses both sit at column 0 on this line.
    """
    name: str
    line: int


def scan_test_functions(text: str) -> List[TestFunction]:
    """Return every top-level ``fn test_*`` declaration in ``text``.

    "Top-level" means column zero — indented (nested) fns are excluded
    even when their name starts with ``test_``, mirroring the test
    harness's discovery rule. The scan is single-pass and source-order
    deterministic so the lens list is stable across re-renders.
    """
    out: List[TestFunction] = []
    for line_no, line in enumerate(text.splitlines()):
        m = _TEST_FN_DEF_RE.match(line)
        if not m:
            continue
        out.append(TestFunction(name=m.group(1), line=line_no))
    return out


def build_test_run_lens(
    test_fn: TestFunction,
    uri: str,
) -> Dict[str, Any]:
    """Build the "▶ Run" CodeLens for ``test_fn``.

    ``command.command`` is ``nova-lsp.runTest`` — the client looks this
    up in its command registry and forwards the ``arguments`` array.
    The single positional argument is a structured payload (``{file, name}``)
    so the client can pick out either field without parsing positional
    indices — this is the convention rust-analyzer / pyright use for their
    own Run-test lenses.
    """
    file_path = _uri_to_abs(uri) or ""
    return {
        "range": {
            "start": {"line": test_fn.line, "character": 0},
            "end": {"line": test_fn.line, "character": 0},
        },
        "command": {
            "title": TEST_RUN_TITLE,
            "command": TEST_RUN_COMMAND,
            "arguments": [{"file": file_path, "name": test_fn.name}],
        },
        "data": {
            "kind": "test_run",
            "name": test_fn.name,
            "line": test_fn.line,
            "uri": uri,
        },
    }


def build_test_debug_lens(
    test_fn: TestFunction,
    uri: str,
) -> Dict[str, Any]:
    """Build the "⏷ Debug" CodeLens for ``test_fn``.

    Same shape as ``build_test_run_lens`` but with the Debug command +
    title. Editors typically render the two lenses side-by-side on the
    same synthetic line above the declaration (separated by ``|``); the
    LSP spec doesn't enforce the layout, so the visual order is up to
    the client.
    """
    file_path = _uri_to_abs(uri) or ""
    return {
        "range": {
            "start": {"line": test_fn.line, "character": 0},
            "end": {"line": test_fn.line, "character": 0},
        },
        "command": {
            "title": TEST_DEBUG_TITLE,
            "command": TEST_DEBUG_COMMAND,
            "arguments": [{"file": file_path, "name": test_fn.name}],
        },
        "data": {
            "kind": "test_debug",
            "name": test_fn.name,
            "line": test_fn.line,
            "uri": uri,
        },
    }


def compute_test_code_lenses(
    uri: str,
    doc_text: str,
) -> List[Dict[str, Any]]:
    """Return Run + Debug CodeLens[] for every top-level ``fn test_*``.

    Each test fn contributes TWO lenses (Run, then Debug) in that order,
    grouped per fn so the editor renders ``▶ Run | ⏷ Debug`` together.
    Non-test fns contribute nothing — see ``scan_test_functions``.

    Pure single-buffer analysis: no workspace warm-up, no import-graph
    walk. The Run / Debug actions are dispatched to client-side
    commands; the server's only job is to point at the right (file, fn)
    pair, which the buffer text fully determines.
    """
    out: List[Dict[str, Any]] = []
    for test_fn in scan_test_functions(doc_text):
        out.append(build_test_run_lens(test_fn, uri))
        out.append(build_test_debug_lens(test_fn, uri))
    return out
