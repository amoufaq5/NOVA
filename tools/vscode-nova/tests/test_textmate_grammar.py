"""
Smoke test: validates the TextMate grammar JSON parses and covers
the keyword / type / literal / comment categories the extension's
README promises for v0.1.0.

We deliberately do NOT exercise oniguruma against sample code —
that would require shipping a regex engine + sample corpus. The
purpose here is to catch the easy regressions:
  - JSON broke during an edit
  - someone deleted the `keywords` or `numbers` repository entry
  - scopeName drifted away from `source.nova`
"""

from __future__ import annotations

import json
import os
import unittest

EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GRAMMAR_PATH = os.path.join(EXT_ROOT, "syntaxes", "nova.tmLanguage.json")


def _load_grammar() -> dict:
    with open(GRAMMAR_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class TestGrammarStructure(unittest.TestCase):
    """Top-level grammar invariants."""

    def setUp(self) -> None:
        self.grammar = _load_grammar()

    def test_scope_name_matches_package_json(self) -> None:
        self.assertEqual(self.grammar.get("scopeName"), "source.nova")

    def test_file_types_includes_nova(self) -> None:
        # `fileTypes` is informational in newer VS Code (the language
        # registration in package.json wins), but keep it consistent.
        self.assertIn("nova", self.grammar.get("fileTypes", []))

    def test_repository_present(self) -> None:
        self.assertIn(
            "repository",
            self.grammar,
            "repository-based grammars require a `repository` map",
        )


class TestGrammarCoverage(unittest.TestCase):
    """Spot-check that the grammar repository covers the
    categories README.md promises."""

    def setUp(self) -> None:
        self.grammar = _load_grammar()
        self.repo = self.grammar.get("repository", {})

    def test_comments_covered(self) -> None:
        self.assertIn("comments", self.repo)

    def test_strings_covered(self) -> None:
        self.assertIn("strings", self.repo)

    def test_numbers_covered(self) -> None:
        self.assertIn("numbers", self.repo)

    def test_keywords_covered(self) -> None:
        self.assertIn("keywords", self.repo)
        # README promises specific keyword tokens; check they appear
        # somewhere in the keywords section's regex bodies.
        kw_section = json.dumps(self.repo["keywords"])
        for kw in ("fn", "let", "if", "else", "while", "return", "import"):
            self.assertIn(
                kw,
                kw_section,
                f"keyword {kw!r} is missing from the grammar",
            )

    def test_types_covered(self) -> None:
        self.assertIn("types", self.repo)
        ty_section = json.dumps(self.repo["types"])
        for ty in ("int", "str", "bool", "list"):
            self.assertIn(
                ty,
                ty_section,
                f"type {ty!r} is missing from the grammar",
            )

    def test_builtins_covered(self) -> None:
        self.assertIn("builtins", self.repo)

    def test_operators_covered(self) -> None:
        self.assertIn("operators", self.repo)


class TestPatterns(unittest.TestCase):
    """Top-level `patterns` list must include each repository
    section we expect to highlight."""

    def setUp(self) -> None:
        self.grammar = _load_grammar()

    def test_top_level_includes(self) -> None:
        includes = [
            p.get("include", "") for p in self.grammar.get("patterns", [])
        ]
        for tag in (
            "#comments",
            "#strings",
            "#numbers",
            "#keywords",
            "#types",
            "#builtins",
            "#operators",
        ):
            self.assertIn(
                tag,
                includes,
                f"top-level patterns missing {tag!r}",
            )


if __name__ == "__main__":
    unittest.main()
