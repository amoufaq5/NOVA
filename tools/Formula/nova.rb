# Homebrew formula for the Nova programming language.
#
# Tap & install:
#   brew tap nova-lang/nova https://github.com/nova-lang/homebrew-nova
#   brew install nova
#
# Or directly from this formula (for hacking):
#   brew install --build-from-source ./tools/Formula/nova.rb
#
# Bumping the formula after a new release:
#   1. Tag + push -> .github/workflows/release.yml builds the tarballs.
#   2. brew bump-formula-pr nova \
#        --url=https://github.com/nova-lang/nova/releases/download/vX.Y.Z/nova-macos-x86_64.tar.gz
#      (auto-computes SHA256 + opens a PR against the tap repo).
#   3. Alternatively, manual flow:
#        curl -sSLo /tmp/nova.tar.gz \
#          https://github.com/nova-lang/nova/releases/download/vX.Y.Z/nova-macos-x86_64.tar.gz
#        shasum -a 256 /tmp/nova.tar.gz
#      and update both `version` and `sha256` blocks below.
#   4. Audit:    brew audit --strict --online tools/Formula/nova.rb
#   5. Install:  brew install --build-from-source tools/Formula/nova.rb
#   6. Test:     brew test nova
#
# See tools/RELEASE.md for the full release walkthrough.

class Nova < Formula
  desc "Self-hosting Nova programming language compiler"
  homepage "https://github.com/nova-lang/nova"
  license "MIT"
  version "0.1.0"

  # Per-platform tarball URLs. Each block ships the same canonical
  # binary (or .o, on macOS) plus README/INSTALL docs. Once the release
  # workflow produces matching arm64 artifacts (v0.2.0+), add the
  # `on_arm` blocks below.
  on_macos do
    on_intel do
      url "https://github.com/nova-lang/nova/releases/download/v0.1.0/nova-macos-x86_64.tar.gz"
      sha256 "REPLACE_WITH_RELEASE_SHA256_MACOS_X86_64"
    end
    # Apple Silicon: shipped from v0.2.0 onward (depends on R10A
    # follow-up to add a darwin/arm64 cross target).
    # on_arm do
    #   url "https://github.com/nova-lang/nova/releases/download/v0.1.0/nova-macos-arm64.tar.gz"
    #   sha256 "REPLACE_WITH_RELEASE_SHA256_MACOS_ARM64"
    # end
  end

  on_linux do
    on_intel do
      url "https://github.com/nova-lang/nova/releases/download/v0.1.0/nova-linux-x86_64.tar.gz"
      sha256 "REPLACE_WITH_RELEASE_SHA256_LINUX_X86_64"
    end
    # Linux arm64: shipped from v0.2.0 onward.
    # on_arm do
    #   url "https://github.com/nova-lang/nova/releases/download/v0.1.0/nova-linux-arm64.tar.gz"
    #   sha256 "REPLACE_WITH_RELEASE_SHA256_LINUX_ARM64"
    # end
  end

  # binutils provides `as`/`ld` for the compiler's output assembly.
  # On macOS, the system toolchain (Xcode CLT) already ships them, so
  # the dep is Linux-only.
  on_linux do
    depends_on "binutils"
  end

  def install
    # macOS x86_64 ships nova as a relocatable Mach-O object file
    # (nova_macos.o); we link it to a runnable binary at install time
    # using the system clang. Linux ships a stripped ELF directly.
    if OS.mac? && File.exist?("nova_macos.o")
      system "clang", "-o", "nova", "nova_macos.o"
      bin.install "nova"
    elsif File.exist?("nova")
      bin.install "nova"
    else
      odie "release tarball missing both `nova` and `nova_macos.o`"
    end

    # nova_launcher is a thin wrapper around `nova` that sets up runtime
    # search paths for the desktop toolchain. Install if shipped; else
    # synthesize a 4-line shim.
    if File.exist?("nova_launcher")
      bin.install "nova_launcher"
    else
      (bin/"nova_launcher").write <<~SH
        #!/bin/sh
        exec "#{bin}/nova" "$@"
      SH
      chmod 0755, bin/"nova_launcher"
    end

    # Man page (shipped from v0.2.0 onward in the release tarball).
    man1.install "nova.1" if File.exist?("nova.1")

    # Curated examples + docs land under share/doc/nova/.
    doc.install "README.md"  if File.exist?("README.md")
    doc.install "INSTALL.md" if File.exist?("INSTALL.md")
    pkgshare.install "examples" if File.directory?("examples")
  end

  test do
    # --version is an inert probe -- prints "nova 0.1.0" and exits 0.
    # Asserting on the prefix keeps the test stable across patch bumps.
    assert_match(/^nova /, shell_output("#{bin}/nova --version"))

    # End-to-end smoke: compile a hello-world program and assemble it.
    # On macOS we skip the assemble step (system as/ld accepts a
    # different .s dialect; the formula's job is just to verify the
    # compiler binary itself works).
    (testpath/"hello.nova").write <<~NOVA
      fn main() {
          println("hello from nova")
      }
      main()
    NOVA
    system "#{bin}/nova", "#{testpath}/hello.nova", "-o", "#{testpath}/hello.s"
    assert_predicate testpath/"hello.s", :exist?
  end
end
