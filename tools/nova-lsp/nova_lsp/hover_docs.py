"""Doc-comment extraction for `textDocument/hover`.

NOVA conventions:

  * `///` is a **doc comment** — meant to document the declaration that
    immediately follows it (Rust-style triple-slash). The LSP surfaces
    contiguous `///` blocks above `fn` / `let` / `const` / `type`
    declarations as markdown in hover responses.
  * `//` is a plain comment and is **not** part of the doc block.

Stop rule (documented):

  Walking backward from `def_line - 1`, we collect every contiguous
  `///` line. The collection STOPS at the first line that is neither a
  `///` doc comment nor a totally blank line — i.e. plain `//` comments,
  attribute lines, other code, etc. all terminate the doc block.

  A single blank line between `///` blocks is **not** allowed to bridge
  them: the collection stops at the first blank line. This is the
  stricter of the two options listed in R14C's spec, and matches the
  Rust convention (rustdoc) most NOVA users will be familiar with.
  Doing it this way means a stray blank line between an unrelated
  `///`-comment paragraph at the top of the file and the function it
  is NOT meant to document cannot accidentally pull that text into the
  function's hover.

Markdown handling:

  Each surviving line has its leading `///` plus one optional space
  stripped — the rest of the line (whitespace + content) is preserved
  verbatim. Joined with `\n` so the LSP client renders markdown
  features (bullets, code blocks, headers) the doc author wrote.

  Empty `///` lines (`///` with nothing after the slashes) become a
  blank markdown line inside the joined block — useful for paragraph
  breaks INSIDE the doc comment (as opposed to terminating it).
"""
from __future__ import annotations

import os
from typing import List, Optional


DOC_PREFIX = "///"


def _strip_doc_prefix(line: str) -> str:
    """Strip the leading `///` and at most ONE following space from
    `line`. Trailing whitespace is preserved (some doc authors use
    significant trailing spaces for hard line breaks in markdown).

    Examples:
        "/// hello"        -> "hello"
        "///hello"         -> "hello"      (no space to consume)
        "///  hello"       -> " hello"     (only one space consumed)
        "///"              -> ""           (empty doc line)
        "    /// hi"       -> "hi"         (leading indentation stripped
                                            BEFORE the prefix check)
    """
    stripped = line.lstrip()
    if not stripped.startswith(DOC_PREFIX):
        return ""
    body = stripped[len(DOC_PREFIX):]
    # Consume at most one space so authors writing `/// text` (the
    # common form) get clean markdown without the leading space.
    if body.startswith(" "):
        body = body[1:]
    return body


def _is_doc_line(line: str) -> bool:
    """True if `line` is a `///` doc comment (with arbitrary leading
    whitespace). Plain `//` comments do NOT count."""
    stripped = line.lstrip()
    if not stripped.startswith(DOC_PREFIX):
        return False
    # Disambiguate from a //// or longer divider: those are not doc
    # comments by convention. A line like `////` is a separator.
    rest = stripped[len(DOC_PREFIX):]
    if rest.startswith("/"):
        return False
    return True


def collect_doc_lines(lines: List[str], def_line: int) -> List[str]:
    """Walk backward from `def_line - 1` and return the contiguous
    block of `///` doc comments, in source order (top-to-bottom).

    Stops at:
      * The first non-doc, non-doc-comment line (plain `//`, code, etc).
      * The first BLANK line — doc blocks must be contiguous with no
        blank-line gap before the declaration. See the module docstring
        for the rationale.
      * Beginning of file.

    Returns the raw lines (still prefixed with `///`). Use
    `extract_doc_comment_from_text` for the joined markdown payload.
    """
    if def_line <= 0:
        return []
    collected: List[str] = []
    i = def_line - 1
    while i >= 0:
        line = lines[i]
        if _is_doc_line(line):
            collected.append(line)
            i -= 1
            continue
        # Anything else — including blank lines and plain `//` — stops
        # the walk. Doc blocks are required to be contiguous with the
        # declaration they document.
        break
    collected.reverse()
    return collected


def extract_doc_comment_from_text(text: str, def_line: int) -> str:
    """Given the full document `text` and a zero-based definition line,
    extract the `///` doc comment block above it as a markdown string.

    Returns an empty string when no doc comment is present so callers
    can use a simple truthiness check to decide whether to include
    documentation in the hover payload.
    """
    if def_line < 0:
        return ""
    lines = text.splitlines()
    if def_line >= len(lines):
        return ""
    raw = collect_doc_lines(lines, def_line)
    if not raw:
        return ""
    stripped = [_strip_doc_prefix(l) for l in raw]
    return "\n".join(stripped)


def extract_doc_comment(file_path: str, def_line: int) -> str:
    """Read `file_path` from disk and extract the `///` doc block
    immediately above `def_line` (zero-based).

    Returns the empty string on any I/O error, missing file, or when
    there is no doc block — the LSP hover handler treats that as "no
    docs to surface" and falls back to the bare signature.
    """
    try:
        abs_path = os.path.abspath(file_path)
    except (OSError, ValueError):
        return ""
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    return extract_doc_comment_from_text(text, def_line)


def render_hover_markdown(signature: str, docs: str) -> str:
    """Compose a hover markdown payload combining the syntax-highlighted
    signature with the (optional) doc comment.

    Layout (matches widely-deployed LSPs like rust-analyzer, pyright):
        ```nova
        <signature>
        ```
        ---
        <docs>

    The `---` separator is only emitted when there are docs to show; a
    bare signature gets just the fenced code block (matching the old
    hover behavior before this enhancement).
    """
    code_block = f"```nova\n{signature}\n```"
    if not docs:
        return code_block
    return f"{code_block}\n\n---\n\n{docs}"
