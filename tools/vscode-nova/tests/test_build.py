"""
Smoke test: runs the VSIX build script when (and only when) the
environment is plausibly capable of doing so, and checks that a
`.vsix` artifact appears.

This test is intentionally lenient about the environment:
  - If `npm` is not on PATH, the test is SKIPPED (not failed). The
    build script itself surfaces a clear error in that case; running
    it as a unit test under CI without Node would be noise.
  - If the build script itself exits non-zero, the test FAILS and
    surfaces the script output for diagnosis.
  - The check at the end is "some .vsix file exists in the extension
    root and matches the manifest version", not a byte-exact path.

This test does NOT install the VSIX into a real VS Code instance —
that's out of scope for a unit suite.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILD_SCRIPT = os.path.join(EXT_ROOT, "scripts", "build-vsix.sh")
MANIFEST_PATH = os.path.join(EXT_ROOT, "package.json")


def _manifest_version() -> str:
    with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh).get("version", "")


def _is_offline_or_no_node() -> bool:
    """Decide whether to skip the build.

    We skip if npm isn't installed OR if the NOVA_VSCODE_OFFLINE
    env var is set (used by sandboxed CI runs that have no network).
    """
    if os.environ.get("NOVA_VSCODE_OFFLINE") == "1":
        return True
    if shutil.which("npm") is None:
        return True
    return False


class TestBuildScriptShape(unittest.TestCase):
    """Cheap structural checks that always run."""

    def test_script_exists_and_is_executable(self) -> None:
        self.assertTrue(
            os.path.isfile(BUILD_SCRIPT),
            f"build script {BUILD_SCRIPT} not found",
        )
        self.assertTrue(
            os.access(BUILD_SCRIPT, os.X_OK),
            f"build script {BUILD_SCRIPT} is not executable; "
            "run `chmod +x scripts/build-vsix.sh`",
        )

    def test_script_invokes_npm_and_vsce(self) -> None:
        with open(BUILD_SCRIPT, "r", encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("npm install", body)
        self.assertIn("npm run compile", body)
        self.assertIn("vsce", body)


@unittest.skipIf(
    _is_offline_or_no_node(),
    "skipping: npm not on PATH or NOVA_VSCODE_OFFLINE=1",
)
class TestBuildRuns(unittest.TestCase):
    """Actually run the build script. Skipped when network/Node
    aren't available."""

    def test_build_produces_vsix(self) -> None:
        # 300s should be plenty for `npm install` on a fresh tree.
        result = subprocess.run(
            ["bash", BUILD_SCRIPT],
            cwd=EXT_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"build script failed:\nSTDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}",
        )
        version = _manifest_version()
        candidates = [
            f
            for f in os.listdir(EXT_ROOT)
            if f.endswith(".vsix") and version in f
        ]
        self.assertTrue(
            candidates,
            f"no .vsix matching version {version} produced; "
            f"saw: {os.listdir(EXT_ROOT)}",
        )


if __name__ == "__main__":
    unittest.main()
