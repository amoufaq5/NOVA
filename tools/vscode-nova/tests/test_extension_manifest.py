"""
Smoke test: validates that tools/vscode-nova/package.json contains
the fields VS Code requires for an installable language + debugger
extension.

What this test does NOT do:
  - Run vsce package (the build harness does that).
  - Talk to a live VS Code instance.
  - Validate JSON schema against vscode's published manifest schema.

What it DOES enforce:
  - All `contributes` sections required for v0.1.0 are wired.
  - `activationEvents` includes the `.nova` language trigger.
  - Configuration properties referenced by `src/extension.ts` exist.
  - Snippets path resolves on disk.
"""

from __future__ import annotations

import json
import os
import unittest

EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MANIFEST_PATH = os.path.join(EXT_ROOT, "package.json")


def _load_manifest() -> dict:
    with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class TestExtensionManifestTopLevel(unittest.TestCase):
    """Top-level required fields."""

    def setUp(self) -> None:
        self.manifest = _load_manifest()

    def test_name_is_present(self) -> None:
        self.assertIn("name", self.manifest)
        self.assertTrue(self.manifest["name"])

    def test_display_name_is_present(self) -> None:
        self.assertIn("displayName", self.manifest)
        self.assertIn("NOVA", self.manifest["displayName"])

    def test_version_is_semver_ish(self) -> None:
        version = self.manifest.get("version", "")
        parts = version.split(".")
        self.assertEqual(
            len(parts),
            3,
            f"version {version!r} should be MAJOR.MINOR.PATCH",
        )
        for p in parts:
            self.assertTrue(p.isdigit() or p.split("-")[0].isdigit())

    def test_publisher_is_set(self) -> None:
        """Marketplace publish needs a real publisher; for local
        VSIX install via `code --install-extension`, any non-empty
        string is accepted. v0.1.0 ships a placeholder."""
        self.assertIn("publisher", self.manifest)
        self.assertTrue(self.manifest["publisher"])

    def test_engines_vscode_pinned(self) -> None:
        self.assertIn("engines", self.manifest)
        self.assertIn("vscode", self.manifest["engines"])
        self.assertTrue(self.manifest["engines"]["vscode"].startswith("^1."))

    def test_main_points_at_compiled_output(self) -> None:
        self.assertEqual(self.manifest.get("main"), "./out/extension.js")

    def test_activation_on_nova_language(self) -> None:
        events = self.manifest.get("activationEvents", [])
        self.assertIn("onLanguage:nova", events)

    def test_categories_cover_lsp_and_debug(self) -> None:
        cats = self.manifest.get("categories", [])
        self.assertIn("Programming Languages", cats)
        self.assertIn("Debuggers", cats)


class TestContributes(unittest.TestCase):
    """`contributes` block: languages / grammars / debuggers /
    configuration / breakpoints."""

    def setUp(self) -> None:
        self.manifest = _load_manifest()
        self.contributes = self.manifest.get("contributes", {})

    def test_registers_nova_language(self) -> None:
        langs = self.contributes.get("languages", [])
        ids = [lang["id"] for lang in langs]
        self.assertIn("nova", ids)
        nova = next(lang for lang in langs if lang["id"] == "nova")
        self.assertIn(".nova", nova["extensions"])
        self.assertIn("configuration", nova)

    def test_registers_grammar_for_nova(self) -> None:
        grammars = self.contributes.get("grammars", [])
        nova_g = [g for g in grammars if g.get("language") == "nova"]
        self.assertEqual(
            len(nova_g),
            1,
            "exactly one TextMate grammar should be registered for `nova`",
        )
        self.assertEqual(nova_g[0]["scopeName"], "source.nova")
        grammar_path = os.path.join(EXT_ROOT, nova_g[0]["path"])
        self.assertTrue(
            os.path.exists(grammar_path),
            f"grammar path {grammar_path} does not exist",
        )

    def test_registers_nova_debugger(self) -> None:
        debuggers = self.contributes.get("debuggers", [])
        types = [d.get("type") for d in debuggers]
        self.assertIn("nova", types)
        nova_dbg = next(d for d in debuggers if d["type"] == "nova")
        self.assertIn("languages", nova_dbg)
        self.assertIn("nova", nova_dbg["languages"])
        self.assertIn("configurationAttributes", nova_dbg)
        launch = nova_dbg["configurationAttributes"]["launch"]
        self.assertIn("program", launch["required"])

    def test_registers_breakpoints_for_nova(self) -> None:
        breakpoints = self.contributes.get("breakpoints", [])
        langs = [b.get("language") for b in breakpoints]
        self.assertIn(
            "nova",
            langs,
            "breakpoints must be declared for `nova` so gutter clicks work",
        )

    def test_configuration_properties_match_extension(self) -> None:
        """Settings referenced from src/extension.ts must exist in
        the manifest, else readConfig() picks up undefined."""
        configuration = self.contributes.get("configuration", {})
        props = configuration.get("properties", {})
        for key in (
            "nova.python.path",
            "nova.lsp.enabled",
            "nova.lsp.module",
            "nova.lsp.args",
            "nova.dap.module",
            "nova.dap.args",
            "nova.trace.server",
        ):
            self.assertIn(key, props, f"missing setting: {key}")

    def test_snippets_path_resolves(self) -> None:
        snippets = self.contributes.get("snippets", [])
        for s in snippets:
            spath = os.path.join(EXT_ROOT, s["path"])
            self.assertTrue(
                os.path.exists(spath),
                f"snippet path {spath} does not exist",
            )


class TestScripts(unittest.TestCase):
    """`scripts` block: compile + package commands."""

    def setUp(self) -> None:
        self.manifest = _load_manifest()
        self.scripts = self.manifest.get("scripts", {})

    def test_compile_invokes_tsc(self) -> None:
        self.assertIn("compile", self.scripts)
        self.assertIn("tsc", self.scripts["compile"])

    def test_package_invokes_vsce(self) -> None:
        self.assertIn("package", self.scripts)
        self.assertIn("vsce", self.scripts["package"])


class TestDependencies(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = _load_manifest()

    def test_language_client_dep_present(self) -> None:
        deps = self.manifest.get("dependencies", {})
        self.assertIn(
            "vscode-languageclient",
            deps,
            "the LSP glue requires the `vscode-languageclient` package",
        )

    def test_dev_deps_include_typescript_and_vsce(self) -> None:
        dev = self.manifest.get("devDependencies", {})
        self.assertIn("typescript", dev)
        # vsce moved namespaces: accept either historical or current
        self.assertTrue(
            "vsce" in dev or "@vscode/vsce" in dev,
            "expected one of `vsce` / `@vscode/vsce` in devDependencies",
        )
        self.assertIn("@types/vscode", dev)
        self.assertIn("@types/node", dev)


if __name__ == "__main__":
    unittest.main()
