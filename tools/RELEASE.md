# Cutting a Nova release

This document is the end-to-end recipe for shipping a new version of
Nova through GitHub Releases, the `curl-bash` installer, and the
Homebrew tap.

## Versioning policy

Nova uses [Semantic Versioning](https://semver.org/) with the
following project-specific reading:

| Bump          | Trigger                                                   |
| ------------- | --------------------------------------------------------- |
| **MAJOR**     | Language-syntax breakage (anything that fails to parse on a stage1-stable program from the previous release). |
| **MINOR**     | New stdlib modules, new builtins, new codegen targets, new optimisation modes that change emitted assembly but stay backward-compatible. |
| **PATCH**     | Bug fixes, doc-only changes, packaging tweaks, CI-only changes. Stage2 == stage3 self-hosting remains intact. |

Pre-release versions use `vX.Y.Z-rcN` and trigger the same workflow.

## Release flow

1. **Ensure clean self-hosting locally.**

   ```bash
   make self-host         # stage2.s must equal stage3.s
   make test              # all suites must pass
   ```

2. **Update version refs.** Bump the version in:
   - `INSTALL.md` (curl-bash example, brew install snippet)
   - `tools/Formula/nova.rb` (`version "X.Y.Z"`)
   - `tools/install.sh` (default `LATEST_VERSION`)
   - `tools/nova-lsp/pyproject.toml` and `tools/nova-lsp/nova_lsp/__init__.py`
   - `tools/vscode-nova/package.json`

3. **Commit the version bump.**

   ```bash
   git commit -am "release: vX.Y.Z"
   ```

4. **Tag and push.** The `release.yml` workflow is triggered by the
   tag and builds the Linux / Windows / macOS artifact matrix.

   ```bash
   git tag vX.Y.Z
   git push origin main --tags
   ```

5. **Wait for CI.** GitHub Actions will:
   - Build `bin/nova` on Linux x86-64.
   - Cross-build `bin/nova.exe` via `x86_64-w64-mingw32-{as,ld}`.
   - Cross-emit a macOS `.o` via `clang -target x86_64-apple-darwin`
     (users link locally on macOS with `clang -o nova nova_macos.o`).
   - Tar each up as `nova-${platform}.tar.gz` plus a `.sha256` file.
   - Attach all artifacts to a GitHub Release named `Nova vX.Y.Z`.

6. **Regenerate the Homebrew sha256.**

   ```bash
   curl -sSLo /tmp/nova.tar.gz \
     "https://github.com/nova-lang/nova/releases/download/vX.Y.Z/nova-macos-x86_64.tar.gz"
   shasum -a 256 /tmp/nova.tar.gz
   ```

   Update `tools/Formula/nova.rb` with the new sha256, copy it into
   the [`homebrew-nova` tap repository](https://github.com/nova-lang/homebrew-nova),
   and push.

7. **Smoke-test the install paths.** From a clean shell on each
   platform:

   ```bash
   curl -sSL https://raw.githubusercontent.com/nova-lang/nova/main/tools/install.sh | sh
   brew tap nova-lang/nova
   brew install nova
   ```

## Manual / hotfix release

If CI is unavailable, the same artifacts can be produced locally with
`make bin/nova`, `make cross-windows`, and `make cross-macos`; then
`tar -czf` each and upload via `gh release upload vX.Y.Z`.

## Rollback

GitHub Releases are immutable once published. To roll back, cut a
PATCH release that re-publishes the previous-known-good artifacts
under a new tag (`vX.Y.(Z+1)`).
