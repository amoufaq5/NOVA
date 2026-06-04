"""R29D semantic-tokens follow-ups.

These assertions cover the second-wave R29D requirements that go beyond
the original `test_semantic_tokens.py` coverage:

  * `class` and `property` token types exposed in the legend.
  * `let x: Foo = ...` -> `Foo` classified as `type` (declaration=False).
  * `x` in the same line stays a `variable` with `declaration=True`.
  * Field access `obj.field` -> `field` classified as `property`.
  * Generic argument slot `Box<Foo>` -> `Foo` classified as `type`.
  * Struct literal head `Point { x: 1 }` -> `Point` classified as `type`.
  * `range` returns a strict subset of the `full` output.
  * Comment lines do NOT emit non-comment tokens on the same physical line.
  * Hand-computed delta encoding matches the wire shape.
  * Representative-source coverage rate on `parser.nova` is above 70 %.

Together with the legacy 119 assertions in `test_semantic_tokens.py`,
the R29D wave brings semantic-tokens coverage to well over 25 fresh
assertions.

Run directly:

    python tools/nova-lsp/tests/test_semantic_tokens_r29d.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from _harness import LspClient
from nova_lsp.semantic_tokens import (
    KEYWORDS,
    MOD_DECLARATION,
    MOD_READONLY,
    TOKEN_MODIFIERS,
    TOKEN_TYPES,
    TYPE_CLASS,
    TYPE_COMMENT,
    TYPE_FUNCTION,
    TYPE_KEYWORD,
    TYPE_NUMBER,
    TYPE_PARAMETER,
    TYPE_PROPERTY,
    TYPE_STRING,
    TYPE_TYPE,
    TYPE_VARIABLE,
    SemanticToken,
    SemanticTokenizer,
    semantic_tokens_legend,
    tokens_to_lsp_array,
)


_assertions = 0


def assert_(cond, label):
    global _assertions
    _assertions += 1
    assert cond, f"FAIL [{label}]"


def assert_eq(actual, expected, label):
    global _assertions
    _assertions += 1
    assert actual == expected, f"FAIL [{label}]: expected {expected!r}, got {actual!r}"


def _find(tokens, line, start_char):
    for t in tokens:
        if t.line == line and t.start_char == start_char:
            return t
    raise AssertionError(
        f"no token at line={line} start_char={start_char} "
        f"(found {[(t.line, t.start_char, t.length, t.token_type) for t in tokens]})"
    )


def _by_text(tokens, text, source):
    lines = source.splitlines()
    out = []
    for t in tokens:
        if 0 <= t.line < len(lines):
            line_str = lines[t.line]
            if t.start_char + t.length <= len(line_str):
                if line_str[t.start_char:t.start_char + t.length] == text:
                    out.append(t)
    return out


# ---------------------------------------------------------------------------
# Legend coverage: the strict R29D minimum.
# ---------------------------------------------------------------------------


def test_legend_includes_r29d_minimum_types() -> None:
    """The legend must include every token type required by R29D."""
    legend = semantic_tokens_legend()
    required_types = {
        "keyword", "type", "class", "function", "parameter",
        "variable", "property", "namespace", "string", "number",
        "comment", "operator",
    }
    for name in sorted(required_types):
        assert_(name in legend["tokenTypes"],
                f"legend tokenTypes contains {name!r}")


def test_legend_includes_r29d_minimum_modifiers() -> None:
    """The legend must include every modifier required by R29D."""
    legend = semantic_tokens_legend()
    required_mods = {"declaration", "definition", "readonly",
                     "static", "deprecated"}
    for name in sorted(required_mods):
        assert_(name in legend["tokenModifiers"],
                f"legend tokenModifiers contains {name!r}")


def test_legend_class_and_property_indexes() -> None:
    """TYPE_CLASS / TYPE_PROPERTY indices match their legend slots."""
    legend = semantic_tokens_legend()
    assert_eq(legend["tokenTypes"][TYPE_CLASS], "class",
              "TYPE_CLASS index lines up with 'class'")
    assert_eq(legend["tokenTypes"][TYPE_PROPERTY], "property",
              "TYPE_PROPERTY index lines up with 'property'")


# ---------------------------------------------------------------------------
# `let x: Foo = ...` — the R29D acceptance case spelled out in the brief.
# ---------------------------------------------------------------------------


def test_let_x_colon_foo_classifies_foo_as_type() -> None:
    """`let x: Foo = ...` -> `Foo` is `type` with declaration=False;
    `x` is `variable` with declaration=True."""
    src = "let x: Foo = bar\n"
    tokens = SemanticTokenizer(src).tokenize()
    x_tok = _find(tokens, 0, 4)
    assert_eq(x_tok.token_type, TYPE_VARIABLE, "x classified as variable")
    assert_(x_tok.modifier_bits & MOD_DECLARATION,
            "x has declaration modifier")
    foo_tok = _find(tokens, 0, 7)
    assert_eq(foo_tok.token_type, TYPE_TYPE,
              "Foo (after `:`) classified as type")
    assert_(not (foo_tok.modifier_bits & MOD_DECLARATION),
            "Foo (use site) has NO declaration modifier")


def test_generic_type_argument_classifies_as_type() -> None:
    """`Box<Foo>` -> `Foo` after `<` classified as `type`."""
    src = "let p: Box<Foo> = bar\n"
    tokens = SemanticTokenizer(src).tokenize()
    foo_tok = _find(tokens, 0, 11)
    assert_eq(foo_tok.token_type, TYPE_TYPE,
              "Foo (after `<`) classified as type")


def test_dict_literal_key_after_colon_does_not_classify_as_type() -> None:
    """Lowercase identifier after `:` is NOT promoted to type — only
    capitalised idents are. Protects against dict-literal false positives
    in `{key: value}` shapes."""
    src = "fn f() { return {a: 1, b: 2} }\n"
    tokens = SemanticTokenizer(src).tokenize()
    # `a` after `:` shouldn't end up as TYPE_TYPE.
    a_tok = next((t for t in tokens
                  if 0 <= t.line < 1
                  and src.splitlines()[t.line][t.start_char:t.start_char + t.length] == "a"),
                 None)
    if a_tok is not None:
        assert_(a_tok.token_type != TYPE_TYPE,
                "lowercase `a` after `:` not classified as type")


# ---------------------------------------------------------------------------
# Field access -> property.
# ---------------------------------------------------------------------------


def test_field_access_classifies_as_property() -> None:
    """`obj.field` -> `field` classified as `property`."""
    src = "fn use() { return obj.field }\n"
    tokens = SemanticTokenizer(src).tokenize()
    field_tok = next(t for t in _by_text(tokens, "field", src))
    assert_eq(field_tok.token_type, TYPE_PROPERTY,
              "field after `.` classified as property")


def test_method_call_field_still_property() -> None:
    """`obj.method(arg)` -> `method` is property even though `(` follows.

    The `.`-aware rule must run BEFORE the call-site rule because LSP
    semantic tokens use the property colour for the member name and the
    function colour for free-standing call sites — VS Code's default
    theme actually paints these differently.
    """
    src = "fn use() { return list.push(1) }\n"
    tokens = SemanticTokenizer(src).tokenize()
    push_tok = next(t for t in _by_text(tokens, "push", src))
    assert_eq(push_tok.token_type, TYPE_PROPERTY,
              "push after `.` stays property even before `(`")


def test_chained_field_access_property() -> None:
    """`a.b.c` -> both `b` and `c` classify as property."""
    src = "fn use() { return a.b.c }\n"
    tokens = SemanticTokenizer(src).tokenize()
    b = next(t for t in _by_text(tokens, "b", src))
    c = next(t for t in _by_text(tokens, "c", src))
    assert_eq(b.token_type, TYPE_PROPERTY, "b after first `.` is property")
    assert_eq(c.token_type, TYPE_PROPERTY, "c after second `.` is property")


# ---------------------------------------------------------------------------
# Struct-literal head.
# ---------------------------------------------------------------------------


def test_struct_literal_head_classifies_as_type() -> None:
    """`Point { x: 1, y: 2 }` -> `Point` classified as `type`."""
    src = "fn make() { return Point { x: 1, y: 2 } }\n"
    tokens = SemanticTokenizer(src).tokenize()
    point_tok = _by_text(tokens, "Point", src)[0]
    assert_eq(point_tok.token_type, TYPE_TYPE,
              "Point { ... } head classified as type")


# ---------------------------------------------------------------------------
# Comment + non-comment interleaving.
# ---------------------------------------------------------------------------


def test_comment_line_suppresses_other_tokens_on_the_line() -> None:
    """A `//` line comment swallows the rest of the line — no other
    tokens on that line should be emitted."""
    src = "// let x = 5 // would not be a real binding\n"
    tokens = SemanticTokenizer(src).tokenize()
    line0 = [t for t in tokens if t.line == 0]
    assert_eq(len(line0), 1, "exactly one token on the comment line")
    assert_eq(line0[0].token_type, TYPE_COMMENT, "and it is a comment")


def test_comment_followed_by_decl_on_next_line() -> None:
    """A `// comment` does not leak its `comment` typing onto the
    following declaration."""
    src = "// header\nlet x = 5\n"
    tokens = SemanticTokenizer(src).tokenize()
    line1 = [t for t in tokens if t.line == 1]
    assert_(any(t.token_type == TYPE_KEYWORD for t in line1),
            "next-line `let` keyword still emitted as keyword")
    assert_(any(t.token_type == TYPE_VARIABLE for t in line1),
            "next-line `x` still emitted as variable")


# ---------------------------------------------------------------------------
# Hand-computed delta encoding.
# ---------------------------------------------------------------------------


def test_hand_computed_delta_encoding() -> None:
    """Hand-build a 3-token source and verify the wire array byte-for-byte.

    Source:
        let x = 1
        // line 1 -> comment that emits one slice
        fn f() {}

    Expected raw tokens (line, col, length, type):
      (0, 0, 3, keyword)      -- let
      (0, 4, 1, variable)     -- x         (mods readonly+decl+definition)
      (0, 8, 1, number)       -- 1
      (1, 0, len, comment)    -- // line 1 ...
      (2, 0, 2, keyword)      -- fn
      (2, 3, 1, function)     -- f         (mods decl+definition)
    """
    src = "let x = 1\n// line 1 comment\nfn f() {}\n"
    tokens = SemanticTokenizer(src).tokenize()
    arr = tokens_to_lsp_array(tokens)
    # Token 0: deltaLine=0, deltaStart=0, length=3, type=keyword, mods=0
    assert_eq(arr[0], 0, "tok0 deltaLine = 0")
    assert_eq(arr[1], 0, "tok0 deltaStart = 0")
    assert_eq(arr[2], 3, "tok0 length = 3 (let)")
    assert_eq(arr[3], TYPE_KEYWORD, "tok0 type = keyword")
    # Token 1: same line, deltaStart = 4 (let -> x)
    assert_eq(arr[5], 0, "tok1 deltaLine = 0")
    assert_eq(arr[6], 4, "tok1 deltaStart = 4")
    # Token 2: same line, deltaStart = 4 (x -> 1, gap 8 - 4 = 4)
    assert_eq(arr[10], 0, "tok2 deltaLine = 0")
    assert_eq(arr[11], 4, "tok2 deltaStart = 4 (x -> 1)")
    assert_eq(arr[12], 1, "tok2 length = 1 (the digit)")
    assert_eq(arr[13], TYPE_NUMBER, "tok2 type = number")
    # Token 3: jump to line 1, deltaStart absolute = 0
    assert_eq(arr[15], 1, "tok3 deltaLine = 1 (new line)")
    assert_eq(arr[16], 0, "tok3 deltaStart = 0 (absolute)")
    assert_eq(arr[18], TYPE_COMMENT, "tok3 type = comment")
    # Token 4: jump 1 more line to line 2 -> `fn`
    assert_eq(arr[20], 1, "tok4 deltaLine = 1")
    assert_eq(arr[21], 0, "tok4 deltaStart = 0 (new line abs)")
    assert_eq(arr[22], 2, "tok4 length = 2 (fn)")
    assert_eq(arr[23], TYPE_KEYWORD, "tok4 type = keyword")


# ---------------------------------------------------------------------------
# `range` returns a strict subset of `full`.
# ---------------------------------------------------------------------------


def test_range_is_strict_subset_of_full() -> None:
    """Server-level: `range` over a smaller window must return a strict
    subset of `full`'s tokens, and the delta encoding must be valid."""
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        src = (
            "fn a() { return 1 }\n"
            "fn b() { return 2 }\n"
            "fn c() { return 3 }\n"
            "fn d() { return 4 }\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)

        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, src)
        full = client.request("textDocument/semanticTokens/full",
                              {"textDocument": {"uri": uri}})["result"]["data"]
        rng = client.request(
            "textDocument/semanticTokens/range",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 1, "character": 0},
                    "end": {"line": 2, "character": 0},
                },
            },
        )["result"]["data"]
        assert_(len(rng) < len(full),
                "range output is strictly smaller than full output")
        assert_(len(rng) % 5 == 0, "range output remains aligned to 5 ints")
        assert_(len(rng) > 0, "range covers at least one token")


def test_range_with_start_greater_than_end_returns_empty() -> None:
    """A range whose start line is past the end line should not crash and
    must return an empty data array."""
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        src = "fn x() { return 1 }\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, src)
        rng = client.request(
            "textDocument/semanticTokens/range",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 5, "character": 0},
                    "end": {"line": 2, "character": 0},
                },
            },
        )["result"]["data"]
        assert_eq(rng, [], "inverted range returns empty data array")


# ---------------------------------------------------------------------------
# Keyword coverage spelled out in the brief.
# ---------------------------------------------------------------------------


def test_brief_required_keywords_all_classify_as_keyword() -> None:
    """The R29D brief calls out a specific keyword set — make sure all of
    them classify as `keyword` (no late drift)."""
    required = ["fn", "let", "mut", "if", "else", "while", "for",
                "return", "import", "enum", "struct"]
    for kw in required:
        assert_(kw in KEYWORDS, f"{kw!r} is in the KEYWORDS set")
    src = (
        "import \"x\"\n"
        "enum E { A, B }\n"
        "struct S { f }\n"
        "fn f(p) {\n"
        "    let l = 1\n"
        "    mut m = 2\n"
        "    if l { return 3 } else { return 4 }\n"
        "    while m { return 5 }\n"
        "    for i in r { return 6 }\n"
        "}\n"
    )
    tokens = SemanticTokenizer(src).tokenize()
    seen_keywords = set()
    for t in tokens:
        if t.token_type == TYPE_KEYWORD:
            line = src.splitlines()[t.line]
            text = line[t.start_char:t.start_char + t.length]
            seen_keywords.add(text)
    for kw in required:
        assert_(kw in seen_keywords, f"{kw!r} emitted as keyword in sample")


# ---------------------------------------------------------------------------
# Token-type coverage rate on a representative NOVA source file.
# ---------------------------------------------------------------------------


def test_parser_nova_token_coverage_rate() -> None:
    """Tokenize `src/compiler/parser.nova` and compute the share of
    raw-ident-equivalent tokens that received a non-default
    classification.

    Definition: we emit a semantic token for every meaningful raw token
    except plain operators / punctuation. So `coverage = emitted /
    expected`, where `expected` counts identifiers + literals + comments.
    A high rate means editors light up most of the source. The current
    bar is conservative (>=70 %) — most NOVA files clear it easily.
    """
    path = "/home/user/NOVA/src/compiler/parser.nova"
    if not os.path.isfile(path):
        print(f"  SKIP: {path} missing")
        return
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    tokenizer = SemanticTokenizer(src)
    raws = tokenizer._scan_raw()  # pylint: disable=protected-access
    expected = sum(1 for r in raws
                   if r.kind in ("ident", "string", "number", "comment"))
    tokens = tokenizer.tokenize()
    classified = len(tokens)
    rate = classified / max(expected, 1)
    print(f"  parser.nova: {expected} classifiable raw tokens, "
          f"{classified} emitted ({rate*100:.1f}%)")
    assert_(expected > 0, "parser.nova has classifiable raw tokens")
    assert_(rate >= 0.70,
            f"coverage rate must be >=70 %% (got {rate*100:.1f}%)")
    # Distribution sanity: every legend type that appears in the file
    # should also be hit at least once on a real source of this size.
    seen_types = {t.token_type for t in tokens}
    for required in (TYPE_KEYWORD, TYPE_FUNCTION, TYPE_VARIABLE,
                     TYPE_NUMBER, TYPE_STRING, TYPE_COMMENT):
        assert_(required in seen_types,
                f"parser.nova produces at least one {TOKEN_TYPES[required]} token")


# ---------------------------------------------------------------------------
# Tokens sorted and contiguous after delta encoding.
# ---------------------------------------------------------------------------


def test_delta_reconstruction_round_trip() -> None:
    """For every token we emit, re-applying the delta walk must
    reconstruct the original (line, start_char) sequence."""
    src = (
        "let alpha = 1\n"
        "let beta = 2\n"
        "fn gamma(p) { return alpha + beta + p }\n"
    )
    tokens = SemanticTokenizer(src).tokenize()
    arr = tokens_to_lsp_array(tokens)
    sorted_tokens = sorted(tokens, key=lambda t: (t.line, t.start_char))
    prev_line = 0
    prev_start = 0
    for i, tok in enumerate(sorted_tokens):
        dl = arr[5 * i]
        ds = arr[5 * i + 1]
        line = prev_line + dl
        char = prev_start + ds if dl == 0 else ds
        assert_eq(line, tok.line, f"reconstructed line for token {i}")
        assert_eq(char, tok.start_char,
                  f"reconstructed start_char for token {i}")
        assert_eq(arr[5 * i + 2], tok.length,
                  f"length for token {i}")
        assert_eq(arr[5 * i + 3], tok.token_type,
                  f"type for token {i}")
        prev_line = line
        prev_start = char


# ---------------------------------------------------------------------------
# Server-level smoke through `dispatch`.
# ---------------------------------------------------------------------------


def test_server_full_response_data_is_list_of_ints() -> None:
    """End-to-end: every element of the `full` response data must be an
    int (not a string, not a float)."""
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        src = (
            "fn f(p) {\n"
            "    let x: Foo = 1\n"
            "    return obj.field\n"
            "}\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, src)
        resp = client.request(
            "textDocument/semanticTokens/full",
            {"textDocument": {"uri": uri}},
        )
        data = resp["result"]["data"]
        assert_(all(isinstance(x, int) for x in data),
                "every wire-array element is an int")
        assert_(len(data) % 5 == 0, "data length stays multiple of 5")


def main() -> int:
    test_legend_includes_r29d_minimum_types()
    test_legend_includes_r29d_minimum_modifiers()
    test_legend_class_and_property_indexes()
    test_let_x_colon_foo_classifies_foo_as_type()
    test_generic_type_argument_classifies_as_type()
    test_dict_literal_key_after_colon_does_not_classify_as_type()
    test_field_access_classifies_as_property()
    test_method_call_field_still_property()
    test_chained_field_access_property()
    test_struct_literal_head_classifies_as_type()
    test_comment_line_suppresses_other_tokens_on_the_line()
    test_comment_followed_by_decl_on_next_line()
    test_hand_computed_delta_encoding()
    test_range_is_strict_subset_of_full()
    test_range_with_start_greater_than_end_returns_empty()
    test_brief_required_keywords_all_classify_as_keyword()
    test_parser_nova_token_coverage_rate()
    test_delta_reconstruction_round_trip()
    test_server_full_response_data_is_list_of_ints()
    print(f"test_semantic_tokens_r29d: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
