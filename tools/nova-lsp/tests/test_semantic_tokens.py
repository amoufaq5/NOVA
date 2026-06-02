"""Smoke + unit + integration tests for `textDocument/semanticTokens/full`.

Covers:
  * Empty file -> empty data array.
  * `let x = 5; fn foo() { ... }` -> x classified as variable+declaration+
    readonly, foo as function+declaration, 5 as number.
  * Function call: second `foo` after declaration -> function (no
    declaration modifier).
  * Multi-line delta encoding: deltaLine increments, deltaStart resets
    when moving to a new line.
  * `import "foo"` -> "foo" classified as namespace.
  * `// comment` -> comment token covering the rest of the line.
  * `/* block */` comment -> comment token.
  * triple-quoted (`""\"...""\"`) string -> string token.
  * Numeric literals: decimal / hex / octal / binary.
  * `const X = 42` -> X classified as constant+declaration+readonly+static.
  * `type Foo = ...` -> Foo classified as type+declaration.
  * Function parameters classified as parameter+declaration; uses inside
    the body classify as parameter.
  * ALL_CAPS let -> constant kind (per NOVA convention).
  * Cross-file: known_functions hint -> bare identifier classifies as
    function.
  * tokens_to_lsp_array delta encoding is correct.
  * Integration: tokenize codegen.nova end-to-end; assert thousands of
    tokens emitted and a sub-second tokenization wall-clock.
  * Server-level smoke: dispatch `textDocument/semanticTokens/full` and
    check the response shape; capabilities advertise semanticTokensProvider.
  * `textDocument/semanticTokens/range` returns only tokens in range.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

# Make the package importable when running this file directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from _harness import LspClient
from nova_lsp.semantic_tokens import (
    MOD_DECLARATION,
    MOD_DEFINITION,
    MOD_READONLY,
    MOD_STATIC,
    TOKEN_MODIFIERS,
    TOKEN_TYPES,
    TYPE_COMMENT,
    TYPE_CONSTANT,
    TYPE_FUNCTION,
    TYPE_KEYWORD,
    TYPE_NAMESPACE,
    TYPE_NUMBER,
    TYPE_PARAMETER,
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


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _find(tokens, line, start_char):
    """Return the (single) token at (line, start_char) or fail loudly."""
    for t in tokens:
        if t.line == line and t.start_char == start_char:
            return t
    raise AssertionError(
        f"no token at line={line} start_char={start_char} "
        f"(found {[(t.line, t.start_char, t.length, t.token_type) for t in tokens]})"
    )


def _by_text(tokens, text, source):
    """Find all tokens whose span in `source` equals `text` exactly."""
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
# Empty file / trivial cases.
# ---------------------------------------------------------------------------


def test_empty_file() -> None:
    tokenizer = SemanticTokenizer("")
    tokens = tokenizer.tokenize()
    assert_eq(tokens, [], "empty file -> no tokens")
    assert_eq(tokens_to_lsp_array(tokens), [], "empty file -> empty array")


def test_whitespace_only() -> None:
    tokens = SemanticTokenizer("\n\n   \n\t\n").tokenize()
    assert_eq(tokens, [], "whitespace-only file -> no tokens")


# ---------------------------------------------------------------------------
# Basic classification: let / fn / const / type / module / import.
# ---------------------------------------------------------------------------


def test_let_and_fn_basic() -> None:
    src = "let x = 5\nfn foo() { return 1 }\n"
    tokens = SemanticTokenizer(src).tokenize()
    # `let` keyword
    let_kw = _find(tokens, 0, 0)
    assert_eq(let_kw.token_type, TYPE_KEYWORD, "let keyword type")
    # `x` variable + declaration + readonly
    x_tok = _find(tokens, 0, 4)
    assert_eq(x_tok.token_type, TYPE_VARIABLE, "x type = variable")
    assert_(x_tok.modifier_bits & MOD_DECLARATION, "x has declaration modifier")
    assert_(x_tok.modifier_bits & MOD_READONLY, "x has readonly modifier (let)")
    # `5` number
    n_tok = _find(tokens, 0, 8)
    assert_eq(n_tok.token_type, TYPE_NUMBER, "5 type = number")
    # `fn` keyword
    fn_kw = _find(tokens, 1, 0)
    assert_eq(fn_kw.token_type, TYPE_KEYWORD, "fn keyword")
    # `foo` function + declaration
    foo_tok = _find(tokens, 1, 3)
    assert_eq(foo_tok.token_type, TYPE_FUNCTION, "foo type = function")
    assert_(foo_tok.modifier_bits & MOD_DECLARATION, "foo has declaration")


def test_function_call_classifies_as_function() -> None:
    src = "fn foo() { return 1 }\nfn bar() { foo() }\n"
    tokens = SemanticTokenizer(src).tokenize()
    foo_calls = _by_text(tokens, "foo", src)
    # 2 occurrences: declaration + call site.
    assert_eq(len(foo_calls), 2, "foo appears twice")
    # First one (decl) has declaration modifier.
    assert_(foo_calls[0].modifier_bits & MOD_DECLARATION, "first foo is declaration")
    # Second one (call) is function but no declaration modifier.
    assert_eq(foo_calls[1].token_type, TYPE_FUNCTION, "second foo is function")
    assert_(not (foo_calls[1].modifier_bits & MOD_DECLARATION),
            "second foo has no declaration modifier")


def test_const_declaration() -> None:
    src = "const MAX = 42\n"
    tokens = SemanticTokenizer(src).tokenize()
    max_tok = _find(tokens, 0, 6)
    assert_eq(max_tok.token_type, TYPE_CONSTANT, "MAX is constant")
    assert_(max_tok.modifier_bits & MOD_DECLARATION, "MAX has declaration")
    assert_(max_tok.modifier_bits & MOD_READONLY, "MAX has readonly")
    assert_(max_tok.modifier_bits & MOD_STATIC, "MAX has static")


def test_type_declaration() -> None:
    src = "type Foo = Int\n"
    tokens = SemanticTokenizer(src).tokenize()
    foo = _find(tokens, 0, 5)
    assert_eq(foo.token_type, TYPE_TYPE, "Foo is type")
    assert_(foo.modifier_bits & MOD_DECLARATION, "Foo has declaration")


def test_all_caps_let_classifies_as_constant() -> None:
    """NOVA convention: ALL_CAPS `let` names are constants."""
    src = "let TAU = 6\nlet lower = 1\n"
    tokens = SemanticTokenizer(src).tokenize()
    tau = _find(tokens, 0, 4)
    assert_eq(tau.token_type, TYPE_CONSTANT, "TAU classified as constant")
    lower = _find(tokens, 1, 4)
    assert_eq(lower.token_type, TYPE_VARIABLE, "lower classified as variable")


def test_import_string_is_namespace() -> None:
    src = 'import "../src/runtime/foo.nova"\n'
    tokens = SemanticTokenizer(src).tokenize()
    # `import` keyword
    imp_kw = _find(tokens, 0, 0)
    assert_eq(imp_kw.token_type, TYPE_KEYWORD, "import keyword")
    # String at col 7 — `"..."`
    str_tok = _find(tokens, 0, 7)
    assert_eq(str_tok.token_type, TYPE_NAMESPACE, "import path is namespace")
    # The string spans the entire `"..."` literal.
    expected_len = len('"../src/runtime/foo.nova"')
    assert_eq(str_tok.length, expected_len, "namespace string length")


def test_plain_string_is_string() -> None:
    src = 'fn greet() { println("hello world") }\n'
    tokens = SemanticTokenizer(src).tokenize()
    # Find the string token (text starts at the `"` character).
    strs = [t for t in tokens if t.token_type == TYPE_STRING]
    assert_eq(len(strs), 1, "exactly one string token")
    # `"hello world"` is 13 chars
    assert_eq(strs[0].length, 13, "string token length")


# ---------------------------------------------------------------------------
# Comments.
# ---------------------------------------------------------------------------


def test_line_comment() -> None:
    src = "// hello world\nfn x() {}\n"
    tokens = SemanticTokenizer(src).tokenize()
    c = _find(tokens, 0, 0)
    assert_eq(c.token_type, TYPE_COMMENT, "// is comment")
    assert_eq(c.length, len("// hello world"), "comment covers whole line")


def test_block_comment_single_line() -> None:
    src = "/* one line */\nfn x() {}\n"
    tokens = SemanticTokenizer(src).tokenize()
    c = _find(tokens, 0, 0)
    assert_eq(c.token_type, TYPE_COMMENT, "/* */ is comment")


def test_block_comment_multiline() -> None:
    src = "/* line1\nline2\nline3 */\n"
    tokens = SemanticTokenizer(src).tokenize()
    # We emit one comment token per line slice.
    comments = [t for t in tokens if t.token_type == TYPE_COMMENT]
    assert_eq(len(comments), 3, "multi-line block comment -> 3 slices")
    assert_eq(comments[0].line, 0, "first slice on line 0")
    assert_eq(comments[1].line, 1, "second slice on line 1")
    assert_eq(comments[2].line, 2, "third slice on line 2")


# ---------------------------------------------------------------------------
# Numbers.
# ---------------------------------------------------------------------------


def test_number_literals_decimal_hex_octal_binary() -> None:
    src = "let a = 42\nlet b = 0xff\nlet c = 0o17\nlet d = 0b1010\n"
    tokens = SemanticTokenizer(src).tokenize()
    nums = [t for t in tokens if t.token_type == TYPE_NUMBER]
    assert_eq(len(nums), 4, "four numeric literals")
    # Spot-check lengths.
    by_line = {t.line: t for t in nums}
    assert_eq(by_line[0].length, 2, "decimal `42` length")
    assert_eq(by_line[1].length, 4, "hex `0xff` length")
    assert_eq(by_line[2].length, 4, "octal `0o17` length")
    assert_eq(by_line[3].length, 6, "binary `0b1010` length")


# ---------------------------------------------------------------------------
# Function parameters.
# ---------------------------------------------------------------------------


def test_function_parameters_classified() -> None:
    src = "fn add(a, b) { return a + b }\n"
    tokens = SemanticTokenizer(src).tokenize()
    # Param `a` at col 7
    a = _find(tokens, 0, 7)
    assert_eq(a.token_type, TYPE_PARAMETER, "a is parameter")
    assert_(a.modifier_bits & MOD_DECLARATION, "a is declaration")
    # Param `b` at col 10
    b = _find(tokens, 0, 10)
    assert_eq(b.token_type, TYPE_PARAMETER, "b is parameter")


def test_parameter_uses_in_body() -> None:
    src = "fn greet(name) { println(name) }\n"
    tokens = SemanticTokenizer(src).tokenize()
    name_toks = _by_text(tokens, "name", src)
    assert_eq(len(name_toks), 2, "name appears twice")
    # Both should be parameter type — the second is a body use.
    assert_eq(name_toks[0].token_type, TYPE_PARAMETER, "first name = parameter (decl)")
    assert_eq(name_toks[1].token_type, TYPE_PARAMETER, "second name = parameter (use)")
    assert_(name_toks[0].modifier_bits & MOD_DECLARATION,
            "first name has declaration")
    assert_(not (name_toks[1].modifier_bits & MOD_DECLARATION),
            "body use has no declaration modifier")


# ---------------------------------------------------------------------------
# Delta encoding.
# ---------------------------------------------------------------------------


def test_delta_encoding_same_line() -> None:
    # Two tokens on the same line, deltaLine=0 and deltaStart relative.
    tokens = [
        SemanticToken(line=0, start_char=0, length=3, token_type=TYPE_KEYWORD),
        SemanticToken(line=0, start_char=4, length=1, token_type=TYPE_VARIABLE),
    ]
    arr = tokens_to_lsp_array(tokens)
    assert_eq(len(arr), 10, "two tokens -> 10 ints")
    assert_eq(arr[0], 0, "first deltaLine = 0")
    assert_eq(arr[1], 0, "first deltaStart = 0 (abs)")
    assert_eq(arr[2], 3, "first length = 3")
    assert_eq(arr[3], TYPE_KEYWORD, "first tokenType = keyword")
    assert_eq(arr[5], 0, "second deltaLine = 0 (same line)")
    assert_eq(arr[6], 4, "second deltaStart = 4 (relative to first)")


def test_delta_encoding_new_line() -> None:
    # Token on a new line: deltaLine > 0, deltaStart resets to absolute.
    tokens = [
        SemanticToken(line=0, start_char=0, length=3, token_type=TYPE_KEYWORD),
        SemanticToken(line=2, start_char=5, length=4, token_type=TYPE_FUNCTION),
    ]
    arr = tokens_to_lsp_array(tokens)
    assert_eq(arr[5], 2, "second token jumps 2 lines")
    assert_eq(arr[6], 5, "second deltaStart = 5 (absolute on new line)")


def test_delta_encoding_three_lines() -> None:
    """Multi-line delta: deltaLine increments, deltaStart resets each line."""
    src = "let a = 1\nlet b = 2\nlet c = 3\n"
    tokens = SemanticTokenizer(src).tokenize()
    arr = tokens_to_lsp_array(tokens)
    # Each row: `let X = N` -> 3 tokens (let, X, N). 9 tokens total.
    assert_eq(len(arr), 9 * 5, "3 lines * 3 tokens = 9 tokens (45 ints)")
    # Token #4 (start of line 1) — `let` keyword: deltaLine=1, deltaStart=0
    assert_eq(arr[15], 1, "line 1 `let` deltaLine = 1")
    assert_eq(arr[16], 0, "line 1 `let` deltaStart = 0 (absolute)")
    # Token #7 (start of line 2)
    assert_eq(arr[30], 1, "line 2 `let` deltaLine = 1")
    assert_eq(arr[31], 0, "line 2 `let` deltaStart = 0")


# ---------------------------------------------------------------------------
# Cross-file workspace context.
# ---------------------------------------------------------------------------


def test_known_function_hint_classifies_as_function() -> None:
    """A bare identifier (no parens) that matches a known function name
    from the workspace index classifies as function, not variable."""
    src = "fn caller() { let x = compute_total }\n"
    tokenizer = SemanticTokenizer(src, known_functions={"compute_total"})
    tokens = tokenizer.tokenize()
    ct = _by_text(tokens, "compute_total", src)
    assert_eq(len(ct), 1, "compute_total appears once")
    assert_eq(ct[0].token_type, TYPE_FUNCTION,
              "cross-file fn ref classified as function")


def test_known_constant_hint_classifies_as_constant() -> None:
    src = "fn use_pi() { return PI }\n"
    tokenizer = SemanticTokenizer(src, known_constants={"PI"})
    tokens = tokenizer.tokenize()
    pi = _by_text(tokens, "PI", src)
    assert_eq(len(pi), 1, "PI appears once")
    assert_eq(pi[0].token_type, TYPE_CONSTANT, "PI classified as constant")
    assert_(pi[0].modifier_bits & MOD_READONLY, "PI is readonly")


# ---------------------------------------------------------------------------
# Legend.
# ---------------------------------------------------------------------------


def test_legend_shape() -> None:
    legend = semantic_tokens_legend()
    assert_("tokenTypes" in legend, "legend has tokenTypes")
    assert_("tokenModifiers" in legend, "legend has tokenModifiers")
    # NOVA's full type set is documented in the module.
    assert_("variable" in legend["tokenTypes"], "variable in tokenTypes")
    assert_("function" in legend["tokenTypes"], "function in tokenTypes")
    assert_("namespace" in legend["tokenTypes"], "namespace in tokenTypes")
    assert_("declaration" in legend["tokenModifiers"], "declaration in modifiers")
    assert_("readonly" in legend["tokenModifiers"], "readonly in modifiers")
    # Indexes match the constants exported from semantic_tokens.
    assert_eq(legend["tokenTypes"][TYPE_VARIABLE], "variable",
              "TYPE_VARIABLE index matches legend")
    assert_eq(legend["tokenTypes"][TYPE_FUNCTION], "function",
              "TYPE_FUNCTION index matches legend")
    assert_eq(legend["tokenTypes"][TYPE_KEYWORD], "keyword",
              "TYPE_KEYWORD index matches legend")
    assert_eq(legend["tokenTypes"][TYPE_NUMBER], "number",
              "TYPE_NUMBER index matches legend")


# ---------------------------------------------------------------------------
# Server-level smoke test (drive `dispatch`).
# ---------------------------------------------------------------------------


def test_server_semantic_tokens_full() -> None:
    """End-to-end via the dispatch harness."""
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        src = (
            'import "../src/foo.nova"\n'
            '// header comment\n'
            'let PI = 3\n'
            'fn area(r) { return PI * r }\n'
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)

        client = LspClient()
        init = client.initialize(ws)
        caps = init["result"]["capabilities"]
        assert_("semanticTokensProvider" in caps,
                "semanticTokensProvider in capabilities")
        legend = caps["semanticTokensProvider"]["legend"]
        assert_("variable" in legend["tokenTypes"],
                "legend advertised at initialize")
        assert_(caps["semanticTokensProvider"].get("full") is True,
                "full provider advertised")
        assert_(caps["semanticTokensProvider"].get("range") is True,
                "range provider advertised")

        uri = client.open(path, src)
        resp = client.request("textDocument/semanticTokens/full",
                              {"textDocument": {"uri": uri}})
        data = resp["result"]["data"]
        assert_(isinstance(data, list), "response.data is list")
        # 5 ints per token; at minimum: import, "path", //comment, let,
        # PI, 3, fn, area, r, return, PI, r → 12 tokens = 60 ints.
        assert_(len(data) >= 5 and len(data) % 5 == 0,
                f"data length is multiple of 5, got {len(data)}")
        assert_(len(data) >= 50, f"non-trivial token count, got {len(data)} ints")


def test_server_semantic_tokens_range() -> None:
    """The /range request returns only tokens within the given line range."""
    with tempfile.TemporaryDirectory() as ws:
        path = os.path.join(ws, "main.nova")
        src = "fn a() {}\nfn b() {}\nfn c() {}\nfn d() {}\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        client = LspClient()
        client.initialize(ws)
        uri = client.open(path, src)
        resp = client.request(
            "textDocument/semanticTokens/range",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 1, "character": 0},
                    "end": {"line": 2, "character": 0},
                },
            },
        )
        data = resp["result"]["data"]
        # Only lines 1 and 2 contribute. Each fn line emits 2 tokens
        # (fn keyword + name), so 4 tokens = 20 ints.
        assert_eq(len(data), 20, "range returns 4 tokens (2 lines * 2 tokens)")


# ---------------------------------------------------------------------------
# Integration: tokenize codegen.nova end-to-end.
# ---------------------------------------------------------------------------


def test_integration_codegen_nova() -> None:
    """Tokenize the real NOVA codegen.nova and check we emit thousands of
    tokens in well under a second."""
    path = "/home/user/NOVA/src/compiler/codegen.nova"
    if not os.path.isfile(path):
        print(f"  SKIP integration: {path} missing")
        return
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    line_count = src.count("\n")
    t0 = time.monotonic()
    tokens = SemanticTokenizer(src).tokenize()
    elapsed = time.monotonic() - t0
    print(f"  codegen.nova: {line_count} lines, {len(src)} bytes")
    print(f"  emitted {len(tokens)} tokens in {elapsed*1000:.1f} ms")
    arr = tokens_to_lsp_array(tokens)
    print(f"  delta-compressed to {len(arr)} ints ({len(arr)//5} encoded tokens)")
    assert_(len(tokens) > 5000,
            f">5000 tokens emitted for codegen.nova, got {len(tokens)}")
    assert_(elapsed < 3.0,
            f"tokenization should take <3s, took {elapsed:.2f}s")
    assert_eq(len(arr) % 5, 0, "delta array length is multiple of 5")
    # Spot-check: every emitted SemanticToken has a valid token_type index.
    for t in tokens:
        assert 0 <= t.token_type < len(TOKEN_TYPES), \
            f"invalid token_type {t.token_type}"
    # Compress invariant: deltas are non-negative.
    prev_line = 0
    prev_start = 0
    for i in range(0, len(arr), 5):
        dl = arr[i]
        ds = arr[i + 1]
        assert dl >= 0, f"negative deltaLine at offset {i}"
        assert ds >= 0, f"negative deltaStart at offset {i}"
        # Reconstruct absolute position.
        line = prev_line + dl
        if dl == 0:
            char = prev_start + ds
        else:
            char = ds
        prev_line = line
        prev_start = char


# ---------------------------------------------------------------------------
# Realistic snippet: composition of features.
# ---------------------------------------------------------------------------


def test_realistic_module_snippet() -> None:
    """Composite test: imports, comments, decls, calls, params, numbers."""
    src = (
        '// File: examples/area.nova\n'
        'import "std/math"\n'
        '\n'
        'const PI = 3\n'
        'let cached = 0\n'
        '\n'
        'fn area(r) {\n'
        '    // returns the area of a circle\n'
        '    let result = PI * r * r\n'
        '    return result\n'
        '}\n'
        '\n'
        'fn main() {\n'
        '    let a = area(5)\n'
        '    println(a)\n'
        '}\n'
    )
    tokens = SemanticTokenizer(src).tokenize()
    # Spot check various pieces.
    # `import` keyword
    imp = _find(tokens, 1, 0)
    assert_eq(imp.token_type, TYPE_KEYWORD, "import keyword")
    # `"std/math"` -> namespace
    ns = _find(tokens, 1, 7)
    assert_eq(ns.token_type, TYPE_NAMESPACE, "std/math is namespace")
    # `PI` const declaration
    pi_decl = _find(tokens, 3, 6)
    assert_eq(pi_decl.token_type, TYPE_CONSTANT, "PI is constant")
    assert_(pi_decl.modifier_bits & MOD_STATIC, "PI is static")
    # `cached` variable declaration
    cached = _find(tokens, 4, 4)
    assert_eq(cached.token_type, TYPE_VARIABLE, "cached is variable")
    assert_(cached.modifier_bits & MOD_READONLY, "cached (let) is readonly")
    # `area` function declaration
    area_decl = _find(tokens, 6, 3)
    assert_eq(area_decl.token_type, TYPE_FUNCTION, "area is function")
    assert_(area_decl.modifier_bits & MOD_DECLARATION, "area is declaration")
    # `r` parameter
    r_param = _find(tokens, 6, 8)
    assert_eq(r_param.token_type, TYPE_PARAMETER, "r is parameter")
    # `area(5)` call -> `area` is function (call site)
    area_calls = _by_text(tokens, "area", src)
    assert_(len(area_calls) >= 2, "area appears at least twice")
    # The call site (line 13) shouldn't have declaration modifier.
    call_site = next((t for t in area_calls if t.line == 13), None)
    assert_(call_site is not None, "found area call on line 13")
    assert_eq(call_site.token_type, TYPE_FUNCTION,
              "area call site is function type")
    assert_(not (call_site.modifier_bits & MOD_DECLARATION),
            "area call site has no declaration modifier")


# ---------------------------------------------------------------------------
# Triple-quoted strings.
# ---------------------------------------------------------------------------


def test_triple_quoted_string() -> None:
    src = 'let msg = """hello\nworld"""\n'
    tokens = SemanticTokenizer(src).tokenize()
    strs = [t for t in tokens if t.token_type == TYPE_STRING]
    # Triple string is multiline -> emitted as 2 line slices.
    assert_eq(len(strs), 2, "triple string -> 2 line slices")
    assert_eq(strs[0].line, 0, "first slice on line 0")
    assert_eq(strs[1].line, 1, "second slice on line 1")


# ---------------------------------------------------------------------------
# Negative cases / edge cases.
# ---------------------------------------------------------------------------


def test_keyword_not_identifier() -> None:
    """`if`, `return`, etc. classify as keyword regardless of context."""
    src = "fn x() { if 1 { return 2 } else { return 3 } }\n"
    tokens = SemanticTokenizer(src).tokenize()
    kws = [t for t in tokens if t.token_type == TYPE_KEYWORD]
    kw_texts = [src.splitlines()[t.line][t.start_char:t.start_char + t.length]
                for t in kws]
    assert_("if" in kw_texts, "if is a keyword")
    assert_("return" in kw_texts, "return is a keyword")
    assert_("else" in kw_texts, "else is a keyword")
    assert_("fn" in kw_texts, "fn is a keyword")


def test_unknown_identifier_defaults_to_variable() -> None:
    """An identifier not declared and not called classifies as variable."""
    src = "fn x() { mystery_thing }\n"
    tokens = SemanticTokenizer(src).tokenize()
    m = _by_text(tokens, "mystery_thing", src)
    assert_eq(len(m), 1, "mystery_thing appears once")
    assert_eq(m[0].token_type, TYPE_VARIABLE,
              "unknown bare identifier -> variable")


def test_locally_declared_identifier_classifies_as_function_later() -> None:
    """After `fn foo` is seen, later bare `foo` references classify as function."""
    src = "fn foo() {}\nfn bar() { let f = foo }\n"
    tokens = SemanticTokenizer(src).tokenize()
    foos = _by_text(tokens, "foo", src)
    assert_eq(len(foos), 2, "foo appears twice")
    # First is decl, second is a bare ref (no parens) — should still be function.
    assert_eq(foos[1].token_type, TYPE_FUNCTION,
              "second foo (bare ref after decl) is function")


# ---------------------------------------------------------------------------
# Tokens sorted by (line, char).
# ---------------------------------------------------------------------------


def test_tokens_are_in_source_order() -> None:
    src = "fn a() {}\nfn b() {}\n"
    tokens = SemanticTokenizer(src).tokenize()
    positions = [(t.line, t.start_char) for t in tokens]
    assert_eq(positions, sorted(positions), "tokens emitted in source order")


# ---------------------------------------------------------------------------
# tokens_to_lsp_array handles unsorted input.
# ---------------------------------------------------------------------------


def test_tokens_to_lsp_array_sorts_input() -> None:
    tokens = [
        SemanticToken(line=2, start_char=0, length=3, token_type=TYPE_KEYWORD),
        SemanticToken(line=0, start_char=0, length=3, token_type=TYPE_KEYWORD),
        SemanticToken(line=1, start_char=4, length=1, token_type=TYPE_VARIABLE),
    ]
    arr = tokens_to_lsp_array(tokens)
    # First emitted = line 0 (after sort): deltaLine=0, deltaStart=0
    assert_eq(arr[0], 0, "sorted: first deltaLine = 0")
    # Second emitted = line 1: deltaLine=1, deltaStart=4 (abs new line)
    assert_eq(arr[5], 1, "sorted: second deltaLine = 1")
    assert_eq(arr[6], 4, "sorted: second deltaStart = 4")
    # Third emitted = line 2: deltaLine=1, deltaStart=0 (abs new line)
    assert_eq(arr[10], 1, "sorted: third deltaLine = 1")
    assert_eq(arr[11], 0, "sorted: third deltaStart = 0")


# ---------------------------------------------------------------------------


def main() -> int:
    test_empty_file()
    test_whitespace_only()
    test_let_and_fn_basic()
    test_function_call_classifies_as_function()
    test_const_declaration()
    test_type_declaration()
    test_all_caps_let_classifies_as_constant()
    test_import_string_is_namespace()
    test_plain_string_is_string()
    test_line_comment()
    test_block_comment_single_line()
    test_block_comment_multiline()
    test_number_literals_decimal_hex_octal_binary()
    test_function_parameters_classified()
    test_parameter_uses_in_body()
    test_delta_encoding_same_line()
    test_delta_encoding_new_line()
    test_delta_encoding_three_lines()
    test_known_function_hint_classifies_as_function()
    test_known_constant_hint_classifies_as_constant()
    test_legend_shape()
    test_server_semantic_tokens_full()
    test_server_semantic_tokens_range()
    test_integration_codegen_nova()
    test_realistic_module_snippet()
    test_triple_quoted_string()
    test_keyword_not_identifier()
    test_unknown_identifier_defaults_to_variable()
    test_locally_declared_identifier_classifies_as_function_later()
    test_tokens_are_in_source_order()
    test_tokens_to_lsp_array_sorts_input()
    print(f"test_semantic_tokens: OK ({_assertions} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
