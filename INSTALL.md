# Installing Nova

Nova is a self-hosting language: every install path eventually gives
you a single binary (`nova` on Linux/macOS, `nova.exe` on Windows)
that compiles `.nova` source files to native assembly.

There are **four supported install paths**:

1. [Build from source](#1-build-from-source) — works today, no
   prebuilt binary required.
2. [`curl | sh` one-liner](#2-curlbash-installer) — pulls the
   matching tarball from GitHub Releases (requires the
   `.github/workflows/release.yml` pipeline to have fired at least
   once, which happens on a `v0.1.0` tag push).
3. [Homebrew](#3-homebrew) — `brew install nova` after tapping the
   formula repo.
4. [Manual download](#4-manual-download) — grab the platform tarball
   from the GitHub Releases page and unpack it yourself.

Today the only path that requires zero pre-publication infrastructure
is **(1) Build from source**. (2)-(4) are wired up and ready to fire
the moment the `v0.1.0` tag is pushed to GitHub — the rest of this
document describes them as they will work once the release pipeline
has run.

---

## 1. Build from source

Requirements: `as` and `ld` (standard GNU binutils), `make`, GCC. No
libc, no other dependencies.

```bash
git clone https://github.com/nova-lang/nova.git
cd nova
make            # builds bin/nova (stage 1 via the assembly bootstrap)
make self-host  # verifies stage2.s == stage3.s (self-hosting)
make test       # runs the test suite
```

`bin/nova` is now a stand-alone executable. Copy it onto your
`$PATH` (e.g. `cp bin/nova ~/.local/bin/`) or invoke it directly.

## 2. curl-bash installer

Once the release workflow has published `v0.1.0` (or later), a single
command installs Nova for the current OS/arch:

```bash
curl -sSL https://raw.githubusercontent.com/nova-lang/nova/main/tools/install.sh | sh
```

The script detects your OS + architecture, downloads the matching
`nova-${platform}.tar.gz` from the latest GitHub Release, extracts it
to `~/.local/bin/`, and appends `~/.local/bin` to your shell PATH if
it is not already there. Override the destination with
`NOVA_PREFIX=/usr/local/bin` or pin a version with
`NOVA_VERSION=v0.2.0`.

The installer source lives at `tools/install.sh`.

## 3. Homebrew

A Homebrew formula template lives at `tools/Formula/nova.rb` and will
be published in a dedicated tap repository (`nova-lang/homebrew-nova`)
once `v0.1.0` lands. End-user usage:

```bash
brew tap nova-lang/nova
brew install nova
```

The formula installs `bin/nova` and `bin/nova_launcher` (a wrapper
that sets up runtime search paths). See `tools/RELEASE.md` for the
maintainer workflow that regenerates the formula's `sha256`.

## 4. Manual download

Direct downloads are available on the
[GitHub Releases page](https://github.com/nova-lang/nova/releases):

| Platform        | Tarball                            | Notes                                                   |
| --------------- | ---------------------------------- | ------------------------------------------------------- |
| Linux x86-64    | `nova-linux-x86_64.tar.gz`         | `bin/nova` is the binary; copy to `~/.local/bin/`.      |
| Windows x86-64  | `nova-windows-x86_64.tar.gz`       | `bin/nova.exe`; copy onto `%PATH%`.                     |
| macOS x86-64    | `nova-macos-x86_64.tar.gz`         | Ships as `nova_macos.o`; link locally with `clang -o nova nova_macos.o`. |

Each tarball is accompanied by `*.sha256` for integrity checking.

---

## Verifying your install

After any of the install paths above, confirm the compiler is wired up
correctly:

```bash
nova --version    # should print: nova 0.1.0
nova --help       # should print the usage banner with all flags
```

Both flags are inert probes: they print to stdout and exit `0` without
touching any source file. The Homebrew formula's `test` block, the
`curl-bash` installer's final hint, and CI smoke jobs all use
`nova --version` as the readiness check.

If `nova --version` instead prints `=== Nova Compiler Starting ===`
followed by `Nova compiler v4.0.0 …`, you have an older pre-`v0.1.0`
binary — re-run the installer (which picks up the latest release)
or rebuild from source.

To validate the full release pipeline locally without pushing a tag,
run the dry-run script:

```bash
tools/release-dry-run.sh                 # all targets
tools/release-dry-run.sh linux           # just the host target
```

This produces `dist/release/nova-${target}.tar.gz` (with a matching
`.sha256`) using the same `make bin/nova`, `make cross-windows`, and
`make cross-macos` rules that `.github/workflows/release.yml` runs.
It reports what *would* be uploaded but doesn't push anything to
GitHub.

To smoke-test the installer against a dry-run tarball:

```bash
NOVA_URL=file://$(pwd)/dist/release/nova-linux-x86_64.tar.gz \
    NOVA_PREFIX=$(mktemp -d)/bin \
    sh tools/install.sh
```

---

## Editor support

After installing the compiler:

- VS Code: install the syntax extension from `tools/vscode-nova/`
  (`code --install-extension nova-syntax-0.1.0.vsix`). See
  `tools/vscode-nova/README.md`.
- LSP (diagnostics + hover): `pip install -e tools/nova-lsp/` and wire
  up the `nova-lsp` command in your editor. See
  `tools/nova-lsp/README.md`.
- DAP (step-through debugging): not yet shipped — see
  `tools/nova-dap/README.md` for the design note and roadmap.
