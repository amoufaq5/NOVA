"""Call hierarchy (`textDocument/prepareCallHierarchy`,
`callHierarchy/incomingCalls`, `callHierarchy/outgoingCalls`).

The call hierarchy view shows "who calls this function?" (incoming)
and "what does this function call?" (outgoing) as a navigable tree
in the editor. It is the natural extension of R8C's workspace symbol
index (which already maps name -> declaration site) and R9C's
workspace rename (which already finds cross-file references).

Three LSP requests participate:

  1. `textDocument/prepareCallHierarchy(uri, position)`: resolve the
     identifier under the cursor to a `CallHierarchyItem` payload —
     name, kind, uri, full-decl range, name-token selection range.
     Returns `None` when the cursor isn't on a top-level `fn`.
  2. `callHierarchy/incomingCalls(item)`: scan the workspace for
     every `name(` call-context occurrence of `item.name` and group
     them by the enclosing function. Each enclosing fn becomes one
     `CallHierarchyIncomingCall.from`; `fromRanges` lists every call
     site within it.
  3. `callHierarchy/outgoingCalls(item)`: parse the body of the
     function identified by `item` and emit one
     `CallHierarchyOutgoingCall` per unique callee, with `fromRanges`
     pointing at every call site inside the source body.

Both incoming and outgoing reuse R5F's `FileCache` and R8C's
`WorkspaceSymbolIndex` for the file list + name->location lookups.
The actual `name(` matching is regex-based and masks string literals
+ comments (sharing the implementation idea from `rename_workspace`)
so a call mentioned in a doc string or `//` comment doesn't surface
as a real call site.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from nova_lsp.imports import FileCache, walk_imports
from nova_lsp.workspace_symbols import (
    SYMBOL_KIND_FUNCTION,
    WorkspaceSymbolIndex,
    scan_symbols,
)


# Top-level `fn name(args) {` declarations — column 0 only. Mirrors the
# workspace_symbols convention so incoming/outgoing call hierarchies
# share the same notion of "what is a function".
_FN_DEF_RE = re.compile(r"^fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")

# A call site is `name(` with `name` an identifier. Word-boundary on
# the left so we don't match `myfoo(` for symbol `foo`. The trailing
# `(` is required so plain references (`let x = foo`) don't count.
def _call_regex(name: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\s*\(")


# Any-identifier call regex used for outgoing-call scanning.
_ANY_CALL_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# NOVA keywords that can syntactically precede `(` but are not callable.
# We exclude these from outgoing-call matches.
_NON_CALL_KEYWORDS = frozenset({
    "if", "while", "for", "return", "match", "do", "and", "or", "not",
    "in", "let", "fn", "import", "else", "true", "false", "nil", "null",
    "break", "continue", "end", "mut", "const", "type", "struct",
    "enum", "module", "throw", "try", "catch", "finally", "yield",
})


# ---------------------------------------------------------------------------
# Comment / string masking — copied to keep this module self-contained
# rather than reaching across into rename_workspace's private helper.
# Behavior matches `rename_workspace._mask_comments_and_strings`.
# ---------------------------------------------------------------------------


def _mask_comments_and_strings(line: str) -> str:
    """Replace string-literal and comment content with spaces so a
    regex match against the result preserves column offsets but never
    lands inside a quote or comment. Handles `// ...`, `# ...`, and
    double-quoted strings with backslash escapes.
    """
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
# Function-body extraction — find the line range that constitutes a top-
# level fn body in a source file. Brace counting from the opening `{`.
# ---------------------------------------------------------------------------


@dataclass
class FunctionSpan:
    """The full source span of a top-level fn declaration.

    All line numbers are zero-based. `decl_line` is the `fn name(...)`
    line itself; `body_open_line` is the line containing the `{` (often
    the same as `decl_line`); `end_line` is the line containing the
    matching `}` (inclusive).

    `name_char_start` / `name_char_end` are the column span of the
    function name token on `decl_line` — used for `selectionRange`.
    `args` is the raw parenthesized argument-list string (for `detail`).
    """
    name: str
    decl_line: int
    body_open_line: int
    end_line: int
    name_char_start: int
    name_char_end: int
    args: str


def find_function_spans(text: str) -> List[FunctionSpan]:
    """Walk `text` and return every top-level `fn` definition span.

    Brace counting begins at the first `{` on or after `decl_line` and
    matches forward until depth returns to zero. Lines inside string
    literals + comments are not specially handled — for our use case
    (NOVA source files that are well-formed) the heuristic is enough.
    A stray `{` inside a comment would skew the count, but those are
    rare in practice. Returns spans in source order.
    """
    lines = text.splitlines()
    out: List[FunctionSpan] = []
    i = 0
    while i < len(lines):
        m = _FN_DEF_RE.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        args = m.group(2).strip()
        name_start = m.start(1)
        name_end = m.end(1)
        # Find the opening `{`. Almost always on the signature line.
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
        out.append(FunctionSpan(
            name=name,
            decl_line=i,
            body_open_line=open_line,
            end_line=end_line,
            name_char_start=name_start,
            name_char_end=name_end,
            args=args,
        ))
        i = end_line + 1
    return out


def function_span_at(spans: List[FunctionSpan], line: int) -> Optional[FunctionSpan]:
    """Smallest function whose decl..end span (inclusive) contains
    `line`. Used by both the prepare path (cursor on header line) and
    the incoming-call grouping (call site inside this enclosing fn).
    """
    best: Optional[FunctionSpan] = None
    for s in spans:
        if s.decl_line <= line <= s.end_line:
            if best is None or (s.end_line - s.decl_line) < (best.end_line - best.decl_line):
                best = s
    return best


def function_span_by_name(spans: List[FunctionSpan], name: str) -> Optional[FunctionSpan]:
    """First top-level fn with matching name (source order)."""
    for s in spans:
        if s.name == name:
            return s
    return None


# ---------------------------------------------------------------------------
# CallHierarchyItem construction.
# ---------------------------------------------------------------------------


def _path_to_uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


def _word_at(text: str, line: int, character: int) -> Optional[str]:
    """Identifier under the cursor (mirror of server.word_at)."""
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


def build_call_hierarchy_item(
    span: FunctionSpan,
    path: str,
) -> Dict[str, Any]:
    """Construct a `CallHierarchyItem` payload for `span` in `path`.

    Per LSP spec:
      - `range` covers the full decl..end_line of the function
        (including the body).
      - `selectionRange` covers just the name token on `decl_line`.
      - `detail` carries the signature for tooltip rendering.
      - `data` holds a serializable handle the server uses to resolve
        the item back to its source on follow-up incoming/outgoing
        requests (avoids re-walking the import graph from scratch).
    """
    return {
        "name": span.name,
        "kind": SYMBOL_KIND_FUNCTION,
        "detail": f"fn {span.name}({span.args})",
        "uri": _path_to_uri(path),
        "range": {
            "start": {"line": span.decl_line, "character": 0},
            "end": {"line": span.end_line, "character": 1},
        },
        "selectionRange": {
            "start": {"line": span.decl_line, "character": span.name_char_start},
            "end": {"line": span.decl_line, "character": span.name_char_end},
        },
        "data": {
            "path": os.path.abspath(path),
            "name": span.name,
            "decl_line": span.decl_line,
            "end_line": span.end_line,
        },
    }


# ---------------------------------------------------------------------------
# prepareCallHierarchy.
# ---------------------------------------------------------------------------


def prepare_call_hierarchy(
    uri: str,
    line: int,
    character: int,
    doc_text: str,
    file_cache: FileCache,
    *,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Resolve the cursor's symbol to a CallHierarchyItem list.

    Per LSP spec the response is a `CallHierarchyItem[] | null`. We
    return either:
      * `None` when the cursor isn't on a recognizable function name
        (e.g. on whitespace, a variable, or a `let` binding).
      * A single-element list with the CallHierarchyItem.

    Resolution strategy (in priority order):
      1. Cursor sits inside a `fn name(...)` declaration line in the
         current document — synthesize the item from the local span.
      2. Cursor sits on an identifier whose name matches a top-level
         `fn` in the current document — synthesize from that span.
      3. Cursor sits on an identifier whose name matches a fn in the
         transitively imported graph — resolve via `walk_imports`.
      4. Cursor sits on an identifier whose name matches a fn in the
         workspace index (sibling files outside the import graph).
    """
    name = _word_at(doc_text, line, character)
    if not name:
        return None
    # Step 1+2: same document.
    spans = find_function_spans(doc_text)
    enclosing = function_span_at(spans, line)
    if enclosing and enclosing.name == name and enclosing.decl_line == line:
        # Cursor is on the declaration's name token.
        path = _uri_to_abs(uri)
        if path:
            return [build_call_hierarchy_item(enclosing, path)]
    # Step 2b: name matches some top-level fn in this doc (call site).
    same_doc = function_span_by_name(spans, name)
    if same_doc is not None:
        path = _uri_to_abs(uri)
        if path:
            return [build_call_hierarchy_item(same_doc, path)]
    # Step 3: walk the import graph.
    start_path = _uri_to_abs(uri)
    if start_path:
        for entry in walk_imports(
            start_path, file_cache, text_overrides=text_overrides or {}
        ):
            if entry.path == os.path.abspath(start_path):
                # Already searched as the start doc; avoid re-emitting.
                continue
            entry_spans = find_function_spans(entry.text)
            hit = function_span_by_name(entry_spans, name)
            if hit is not None:
                return [build_call_hierarchy_item(hit, entry.path)]
    # Step 4: workspace index (sibling files outside the import graph).
    if workspace_index is not None:
        for entry in workspace_index.all_symbols():
            if entry.kind != SYMBOL_KIND_FUNCTION or entry.name != name:
                continue
            try:
                with open(entry.path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            entry_spans = find_function_spans(text)
            hit = function_span_by_name(entry_spans, name)
            if hit is not None:
                return [build_call_hierarchy_item(hit, entry.path)]
    return None


def _uri_to_abs(uri: str) -> Optional[str]:
    """Tiny mirror of server.uri_to_path -> abspath."""
    if not uri.startswith("file://"):
        return None
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    return os.path.abspath(unquote(parsed.path))


# ---------------------------------------------------------------------------
# incomingCalls.
# ---------------------------------------------------------------------------


def _scan_calls_in_text(text: str, name: str) -> List[Tuple[int, int, int]]:
    """Return `(line, char_start, char_end)` for each `name(` call site
    in `text`, skipping calls inside strings and comments and skipping
    the `fn name(...)` declaration itself (the declaration looks like
    a call to the bare regex, but it's the def site).
    """
    out: List[Tuple[int, int, int]] = []
    pattern = _call_regex(name)
    # Pre-compile the matching def line pattern so we can compare
    # column positions: a match at the same start as a `fn name(...)`
    # declaration is the declaration, not a call.
    fn_def_pat = re.compile(
        r"^fn\s+" + re.escape(name) + r"\s*\("
    )
    for lineno, line in enumerate(text.splitlines()):
        cleaned = _mask_comments_and_strings(line)
        decl_match = fn_def_pat.match(cleaned)
        decl_name_start = -1
        if decl_match is not None:
            # Within `fn NAME(...)`, the NAME token starts after `fn `.
            name_pat = re.compile(
                r"^fn\s+(" + re.escape(name) + r")"
            )
            nm = name_pat.match(cleaned)
            if nm is not None:
                decl_name_start = nm.start(1)
        for m in pattern.finditer(cleaned):
            if decl_name_start != -1 and m.start() == decl_name_start:
                # This is the fn declaration token, not a call site.
                continue
            # `m.end()` includes the trailing `(`; we want just the name.
            name_end = m.start() + len(name)
            out.append((lineno, m.start(), name_end))
    return out


def incoming_calls(
    item: Dict[str, Any],
    workspace_index: WorkspaceSymbolIndex,
    file_cache: FileCache,
    *,
    extra_paths: Optional[List[str]] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Find every function that CALLS `item.name`.

    Strategy: union the workspace index file list with `extra_paths`
    (open buffers + import closures), scan each file for `name(` call
    sites with comments/strings masked out, then group the call sites
    by the enclosing top-level `fn` in that file. Each enclosing fn
    becomes one `CallHierarchyIncomingCall` with `fromRanges` listing
    the call sites within it.

    Self-calls (the function defined by `item` calls itself) ARE
    reported — recursion is a meaningful relationship to show in the
    hierarchy.

    Returns `CallHierarchyIncomingCall[]` per LSP spec. Caller groups
    are sorted by `(path, decl_line)` for deterministic output.
    """
    name = item.get("name")
    if not name:
        return []
    data = item.get("data") or {}
    overrides = text_overrides or {}

    candidates: Set[str] = set()
    for path in workspace_index._by_file.keys():  # noqa: SLF001
        candidates.add(os.path.abspath(path))
    if extra_paths:
        for p in extra_paths:
            candidates.add(os.path.abspath(p))
    target_path = data.get("path")
    if target_path:
        candidates.add(os.path.abspath(target_path))

    # caller_key -> (item_payload, [Range, ...])
    grouped: Dict[Tuple[str, int], Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}

    for path in sorted(candidates):
        text = overrides.get(path)
        if text is None:
            entry = file_cache.get(path)
            if entry is None:
                continue
            text = entry.text
        sites = _scan_calls_in_text(text, name)
        if not sites:
            continue
        spans = find_function_spans(text)
        for (line, c0, c1) in sites:
            enclosing = function_span_at(spans, line)
            if enclosing is None:
                # Call from top-level (outside any fn) — skip; LSP call
                # hierarchy is "fn-to-fn".
                continue
            key = (path, enclosing.decl_line)
            if key not in grouped:
                caller_item = build_call_hierarchy_item(enclosing, path)
                grouped[key] = (caller_item, [])
            grouped[key][1].append({
                "start": {"line": line, "character": c0},
                "end": {"line": line, "character": c1},
            })

    # Render as LSP CallHierarchyIncomingCall[].
    out: List[Dict[str, Any]] = []
    for (path, _decl_line), (caller_item, ranges) in sorted(grouped.items()):
        out.append({
            "from": caller_item,
            "fromRanges": ranges,
        })
    return out


# ---------------------------------------------------------------------------
# outgoingCalls.
# ---------------------------------------------------------------------------


def _resolve_callee(
    name: str,
    start_path: str,
    file_cache: FileCache,
    workspace_index: Optional[WorkspaceSymbolIndex],
    text_overrides: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[FunctionSpan, str]]:
    """Look up the callee fn's `(span, path)` for `name`, starting in
    the import graph rooted at `start_path` and falling back to the
    workspace index. Returns None when the callee isn't a known
    top-level fn (e.g. a builtin like `len`).
    """
    overrides = text_overrides or {}
    for entry in walk_imports(
        os.path.abspath(start_path), file_cache, text_overrides=overrides
    ):
        spans = find_function_spans(entry.text)
        hit = function_span_by_name(spans, name)
        if hit is not None:
            return hit, entry.path
    if workspace_index is not None:
        for sym in workspace_index.all_symbols():
            if sym.kind != SYMBOL_KIND_FUNCTION or sym.name != name:
                continue
            text = overrides.get(sym.path)
            if text is None:
                try:
                    with open(sym.path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
            spans = find_function_spans(text)
            hit = function_span_by_name(spans, name)
            if hit is not None:
                return hit, sym.path
    return None


def outgoing_calls(
    item: Dict[str, Any],
    file_cache: FileCache,
    *,
    workspace_index: Optional[WorkspaceSymbolIndex] = None,
    text_overrides: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Find every function that `item` CALLS.

    Strategy: locate the source span of `item` (via the `data` blob
    set on prepare), extract the body lines (`body_open_line+1` ..
    `end_line-1`), scan for `name(` patterns (with comments + strings
    masked), then resolve each unique callee name to its top-level
    fn via `_resolve_callee`. Each callee becomes one
    `CallHierarchyOutgoingCall.to` with `fromRanges` listing call
    sites inside the source function's body.

    Self-calls (recursion) are included. Calls to builtins or to
    names that aren't top-level fns in the workspace are skipped —
    we can't navigate to them, so listing them as outgoing nodes
    would be misleading.

    Returns `CallHierarchyOutgoingCall[]` per LSP spec, sorted by
    callee name for deterministic output.
    """
    data = item.get("data") or {}
    source_path = data.get("path")
    source_name = data.get("name") or item.get("name")
    if not source_path or not source_name:
        return []

    overrides = text_overrides or {}
    text = overrides.get(os.path.abspath(source_path))
    if text is None:
        entry = file_cache.get(source_path)
        if entry is None:
            return []
        text = entry.text

    spans = find_function_spans(text)
    src_span = function_span_by_name(spans, source_name)
    if src_span is None:
        return []

    # Body lines: between `body_open_line` (exclusive of the `{` line
    # if the `{` is the only content) and `end_line` (the `}` line).
    # We include `body_open_line` and `end_line` themselves — there
    # may be code after the `{` on the same line, or before the `}`
    # on the closing line. Comments + strings are masked per-line.
    lines = text.splitlines()
    body_start = src_span.body_open_line
    body_end = src_span.end_line

    # callee_name -> [(line, char_start, char_end), ...]
    by_callee: Dict[str, List[Tuple[int, int, int]]] = {}
    for lineno in range(body_start, body_end + 1):
        if lineno >= len(lines):
            break
        cleaned = _mask_comments_and_strings(lines[lineno])
        for m in _ANY_CALL_RE.finditer(cleaned):
            callee = m.group(1)
            if callee in _NON_CALL_KEYWORDS:
                continue
            # Skip the `fn name(...)` declaration on the decl line —
            # that's not a call.
            if lineno == src_span.decl_line and callee == source_name:
                # Only skip the actual declaration token, not any
                # recursive call that happens to share the line.
                if m.start() == src_span.name_char_start:
                    continue
            char_start = m.start()
            char_end = char_start + len(callee)
            by_callee.setdefault(callee, []).append((lineno, char_start, char_end))

    out: List[Dict[str, Any]] = []
    for callee in sorted(by_callee.keys()):
        sites = by_callee[callee]
        # Recursion: the callee is the source function itself.
        if callee == source_name:
            callee_item = build_call_hierarchy_item(src_span, source_path)
        else:
            resolved = _resolve_callee(
                callee,
                source_path,
                file_cache,
                workspace_index,
                text_overrides=overrides,
            )
            if resolved is None:
                # Not a top-level fn in the workspace (builtin, missing
                # import, dynamic call) — omit.
                continue
            span, path = resolved
            callee_item = build_call_hierarchy_item(span, path)
        from_ranges = [{
            "start": {"line": ln, "character": c0},
            "end": {"line": ln, "character": c1},
        } for (ln, c0, c1) in sites]
        out.append({
            "to": callee_item,
            "fromRanges": from_ranges,
        })
    return out
