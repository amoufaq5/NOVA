# Installing Nova

Nova is a self-hosting language: every install path eventually gives
you a single binary (`nova` on Linux/macOS, `nova.exe` on Windows)
that compiles `.nova` source files to native assembly.

There are **seven supported install paths**:

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
5. [Debian / Ubuntu (.deb)](#5-debian--ubuntu-deb) — `sudo apt install
   ./nova_*.deb` for system-wide install with PATH + man page set up
   automatically.
6. [macOS Installer (.pkg)](#6-macos-installer-pkg) — double-click the
   .pkg from a release.
7. [Windows Installer (.msi)](#7-windows-installer-msi) — `msiexec /i
   nova-*.msi` for system-wide install with Start menu shortcut +
   PATH entry.

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

## 5. Debian / Ubuntu (.deb)

A native `.deb` is built by the `release.yml` workflow for every
tagged release and attached to the GitHub Release page.

```bash
# Download nova_X.Y.Z_amd64.deb from the release page, then:
sudo apt install ./nova_0.1.0_amd64.deb

# Or via dpkg directly:
sudo dpkg -i nova_0.1.0_amd64.deb
sudo apt-get install -f          # pull in binutils/libc6 deps if missing

nova --version                   # confirms /usr/local/bin/nova is on PATH
man nova                         # confirms the man page is registered
```

Layout installed by the .deb:

| Path                                  | Purpose                |
| ------------------------------------- | ---------------------- |
| `/usr/local/bin/nova`                 | The compiler binary    |
| `/usr/share/man/man1/nova.1.gz`       | Manual page            |
| `/usr/share/doc/nova/README.md`       | Project README         |
| `/usr/share/doc/nova/INSTALL.md`      | This file              |
| `/usr/share/doc/nova/examples/*.nova` | Curated example programs |
| `/usr/share/doc/nova/copyright`       | MIT license text       |
| `/usr/share/doc/nova/changelog.Debian.gz` | Per-release notes  |

To uninstall: `sudo apt remove nova` (or `sudo dpkg -r nova`).

The `.deb` is built locally with:

```bash
make package-deb         # produces dist/nova_X.Y.Z_amd64.deb
dpkg-deb --info dist/nova_*_amd64.deb   # verify metadata
```

The build script (`scripts/build-deb.sh`) uses plain `dpkg-deb --build`
so it works on any host that has `dpkg-deb` installed — no `debuild`
or `dh_make` required.

## 6. macOS Installer (.pkg)

A signed `.pkg` is built by the `release.yml` workflow on `macos-latest`
and attached to the GitHub Release page.

```bash
# Download nova-X.Y.Z.pkg from the release page, then:
sudo installer -pkg nova-0.1.0.pkg -target /

nova --version                   # confirms /usr/local/bin/nova
man nova                         # confirms the man page is registered
```

GUI install: double-click the `.pkg` to launch the macOS Installer,
follow the prompts. Defaults install to `/usr/local/` (system-wide).

Layout installed by the .pkg:

| Path                                       | Purpose             |
| ------------------------------------------ | ------------------- |
| `/usr/local/bin/nova`                      | The compiler binary |
| `/usr/local/share/man/man1/nova.1`         | Manual page         |
| `/usr/local/share/doc/nova/`               | README + examples   |

To uninstall:

```bash
sudo rm -f /usr/local/bin/nova
sudo rm -rf /usr/local/share/doc/nova
sudo rm -f /usr/local/share/man/man1/nova.1
```

Note: real codesigning of the `.pkg` requires a paid Apple Developer ID
Installer certificate — see `scripts/build-pkg.sh` for the
`SIGN_IDENTITY` env var and `scripts/sign.sh` for the notarytool flow.
Until the cert is configured, the `.pkg` is unsigned and Gatekeeper
will require **right-click → Open** on first launch.

The `.pkg` is built on a macOS host with:

```bash
make package-pkg         # produces dist/nova-X.Y.Z.pkg
```

## 7. Windows Installer (.msi)

A `.msi` is built by the `release.yml` workflow on `windows-latest`
and attached to the GitHub Release page.

```bat
:: From an elevated cmd.exe:
msiexec /i nova-0.1.0.msi /quiet /lv install.log

:: Confirm:
nova --version
```

GUI install: double-click the `.msi` to launch the Windows Installer,
click through the EULA + destination prompts. The installer:

- Copies `nova.exe` to `C:\Program Files\Nova\bin\`
- Adds `C:\Program Files\Nova\bin` to the system `PATH`
- Creates a Start menu shortcut under **Nova → Nova Compiler**
- Registers the uninstaller under **Settings → Apps → Nova**

Layout installed by the .msi:

| Path                                   | Purpose              |
| -------------------------------------- | -------------------- |
| `C:\Program Files\Nova\bin\nova.exe`   | The compiler binary  |
| `C:\Program Files\Nova\doc\README.md`  | Project README       |
| `C:\Program Files\Nova\doc\INSTALL.md` | This file            |

To uninstall: **Settings → Apps → Nova → Uninstall** or
`msiexec /x nova-0.1.0.msi`.

The `.msi` is built on a Windows host (or Linux + WiX-under-wine) with:

```bash
make package-msi         # produces dist/nova-X.Y.Z.msi
```

The build uses the WiX 3.x toolset (`candle` + `light`). The source
file `packaging/windows/nova.wxs` is portable XML and can be audited
from any host. Signing the `.msi` requires a Windows code-signing
certificate — see `scripts/sign.sh` for the `signtool` flow.

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
- **tree-sitter grammar** (Neovim, Helix, Emacs, GitHub web UI):
  the grammar lives at `tools/tree-sitter-nova/`. For Neovim, follow
  `tools/tree-sitter-nova/INSTALL_NEOVIM.md`. For other editors:

  ```bash
  cd tools/tree-sitter-nova
  npm install                  # one-time, installs tree-sitter-cli
  npx tree-sitter generate     # produces src/parser.c
  npx tree-sitter test         # confirms the corpus tests pass
  ```

  The resulting `src/parser.c` is portable C and can be compiled
  into a shared library that any tree-sitter host can load. See
  `tools/tree-sitter-nova/README.md` for the full layout and
  highlight-query details.
