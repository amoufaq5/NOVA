"""Document links (`textDocument/documentLink`).

The document-link capability turns ``import "path/to/file.nova"`` statements
into clickable hyperlinks. The editor draws an underline beneath the quoted
literal; Cmd-click (Ctrl-click on Linux/Windows) follows the link's
``target`` URI, opening the referenced file in a new tab.

LSP wire shape per the spec
(`Document Link Request
<https://microsoft.github.io/language-server-protocol/specification/#textDocument_documentLink>`_):

    interface DocumentLink {
        range: Range;       // the clickable region of the source text
        target?: DocumentUri;  // file:// URI to jump to on click
        tooltip?: string;   // hover prompt the editor renders
        data?: any;         // server-private payload retained on resolve
    }

NOVA's import grammar is the single shape ``import "<path>"`` where the
path may be:

  * relative to the current file's directory (the common case —
    ``import "../runtime/path.nova"``);
  * absolute (rare, but valid — ``import "/abs/path/file.nova"``).

The link's range covers ONLY the literal text between the quotes — clicking
on the quote characters themselves is intentionally a no-op so the user can
still position their cursor at the quote boundary without accidentally
firing navigation.

Editor UX notes:

  * Dead links (target file does not exist on disk) are still emitted with
    the resolved URI. The editor handles the dead-link affordance on its
    own — typically a red squiggle on first click; we don't gate the link
    server-side because:
      1. The file may exist in another working copy / branch the editor
         is about to switch to.
      2. The user may be in the middle of typing the path and a half-typed
         path would surface as "no link" otherwise.
      3. The cost of a stat call per import on every keystroke is
         non-trivial in large files.
  * String literals NOT preceded by ``import`` are NEVER promoted to
    links — ``let path = "src/foo.nova"`` stays plain. This avoids the
    obvious UX foot-gun where ``"path/to/x"`` inside a non-import context
    suddenly becomes clickable.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote


# ``import "<path>"`` with the path captured as group 1. We pin the leading
# anchor to ``^\s*`` so the literal sits on its own statement — a
# parenthesised ``import("x")`` call-expression would not match (NOVA's
# import is a keyword, not a function), and an inline ``foo; import "x"``
# style would not either (NOVA uses newlines, not ``;``, as a statement
# separator).
_IMPORT_LINE_RE = re.compile(r'^(\s*)import\s+"([^"]*)"')


@dataclass
class ImportLink:
    """One ``import "..."`` statement parsed out of a NOVA source file.

    ``line`` / ``char_start`` / ``char_end`` describe the column span of
    the path literal — these are the columns BETWEEN the opening and
    closing quotes, so the editor renders the underline UNDER the path
    text and not over the quote characters.

    ``raw_path`` is the literal text as it appears in source (relative
    paths preserved verbatim — no normalisation). Used by the resolver
    to construct the final ``file://`` URI.
    """
    line: int
    char_start: int
    char_end: int
    raw_path: str


def scan_imports(text: str) -> List[ImportLink]:
    """Return every ``import "..."`` statement in ``text``, source order.

    The scan is line-based and tolerant of:
      * leading whitespace (``    import "..."``);
      * comments and string literals on OTHER lines (we never see them
        because the regex anchors at line start);
      * empty path (``import ""``) — emitted with ``raw_path=""`` so the
        editor can still report a useful UX; the resolver returns
        ``None`` and the link is dropped.

    Multi-line import statements are NOT supported (NOVA's grammar does
    not allow them — the path literal must close on the same line) so
    the simple line-by-line regex is sufficient.
    """
    out: List[ImportLink] = []
    for line_no, line in enumerate(text.splitlines()):
        m = _IMPORT_LINE_RE.match(line)
        if not m:
            continue
        # `m.start(2)` and `m.end(2)` give us the column span of the
        # captured path (group 2) — i.e. the chars BETWEEN the quotes.
        path = m.group(2)
        char_start = m.start(2)
        char_end = m.end(2)
        out.append(
            ImportLink(
                line=line_no,
                char_start=char_start,
                char_end=char_end,
                raw_path=path,
            )
        )
    return out


def resolve_import_path(raw_path: str, base_dir: str) -> Optional[str]:
    """Resolve ``raw_path`` to an absolute filesystem path.

    ``base_dir`` is the directory of the file containing the import — used
    to anchor relative paths. Absolute paths are returned as-is (after
    ``os.path.abspath`` for normalisation).

    Returns ``None`` when ``raw_path`` is empty (the editor doesn't draw
    a link for half-typed paths). Does NOT check existence — the editor
    handles dead-link UX on its own. See the module docstring for why.
    """
    if not raw_path:
        return None
    # Absolute paths bypass the base_dir join. Use ``os.path.isabs`` so
    # this works correctly on both POSIX (`/abs/...`) and Windows
    # (`C:\\...`), even though NOVA is primarily POSIX-targeted.
    if os.path.isabs(raw_path):
        return os.path.abspath(raw_path)
    return os.path.abspath(os.path.join(base_dir, raw_path))


def path_to_file_uri(abs_path: str) -> str:
    """Convert an absolute filesystem path into a ``file://`` URI.

    Uses ``urllib.parse.quote`` with the standard safe set so paths with
    spaces / unicode / shell-special characters survive the round trip
    through the wire format intact. Mirrors the behaviour the LSP spec's
    examples show.
    """
    # ``urllib.parse.quote`` defaults to NOT escaping ``/`` (which is
    # what we want — the URI structure relies on path separators).
    return "file://" + quote(abs_path, safe="/:")


def build_document_link(
    link: ImportLink,
    target_uri: Optional[str],
) -> Dict[str, Any]:
    """Construct one LSP DocumentLink payload.

    The ``range`` covers the path text BETWEEN the quotes (not the quotes
    themselves) so clicking on the closing quote still positions the
    cursor without firing navigation. ``target`` is omitted from the
    response when the path can't be resolved (empty path) so the editor
    falls through to the default ``no link here`` behaviour.

    A ``tooltip`` string is attached so editors that render hover prompts
    (VS Code with `editor.linkProtectionTrustedDomains` configured) can
    surface the resolved path to the user before they click — useful for
    spotting typos in import paths during code review.

    ``data`` mirrors the source path so a follow-up ``documentLink/resolve``
    call could lazily compute the target (we don't lazy-resolve in this
    round; the schema is reserved for forward compatibility).
    """
    payload: Dict[str, Any] = {
        "range": {
            "start": {"line": link.line, "character": link.char_start},
            "end": {"line": link.line, "character": link.char_end},
        },
        "data": {
            "raw_path": link.raw_path,
        },
    }
    if target_uri is not None:
        payload["target"] = target_uri
        payload["tooltip"] = f"Open {link.raw_path}"
    return payload


def compute_document_links(
    uri: str,
    doc_text: str,
) -> List[Dict[str, Any]]:
    """Compute the DocumentLink[] for the document at ``uri``.

    ``uri`` is the source file's ``file://`` URI — used to derive the
    base directory for relative-path resolution. ``doc_text`` is the
    current buffer content (authoritative over disk so unsaved edits
    contribute their links immediately).

    Returns a list of DocumentLink payloads, one per ``import "..."``
    statement in source order. Links with an empty path string are
    dropped (the editor can't navigate to ``""`` — see ``resolve_import_path``).
    Non-existent target files are STILL emitted with the resolved URI;
    dead-link UX is the editor's job. See the module docstring.
    """
    base_dir = _uri_to_base_dir(uri)
    out: List[Dict[str, Any]] = []
    for link in scan_imports(doc_text):
        target_abs = resolve_import_path(link.raw_path, base_dir)
        if target_abs is None:
            # Empty path — drop the link entirely. Emitting a no-target
            # link would render as a hoverable underline that does
            # nothing, which is confusing UX.
            continue
        target_uri = path_to_file_uri(target_abs)
        out.append(build_document_link(link, target_uri))
    return out


def _uri_to_base_dir(uri: str) -> str:
    """Extract the directory of a ``file://`` URI for relative-path resolution.

    Falls back to the current working directory when the URI doesn't
    parse as a ``file://`` (e.g. a synthetic ``untitled:`` buffer with
    no on-disk location). The CWD fallback is rare but ensures the
    handler never crashes on an exotic URI scheme — the resulting links
    will simply be wrong, which the editor surfaces as dead-link errors
    on click rather than an LSP exception trace.
    """
    if not uri.startswith("file://"):
        return os.getcwd()
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if not path:
        return os.getcwd()
    base = os.path.dirname(os.path.abspath(path))
    return base or os.getcwd()
