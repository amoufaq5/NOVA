# Homebrew formula for the Nova programming language.
#
# Tap & install:
#   brew tap nova-lang/nova https://github.com/nova-lang/homebrew-nova
#   brew install nova
#
# Or directly from this formula (for hacking):
#   brew install --build-from-source ./tools/Formula/nova.rb
#
# Maintainers:
#   * After cutting `vX.Y.Z`, regenerate the sha256 with
#       `shasum -a 256 nova-macos-x86_64.tar.gz`
#     and update both `version` and `sha256` below.
#   * See tools/RELEASE.md for the cut-a-release walkthrough.

class Nova < Formula
  desc "Self-hosting Nova programming language compiler"
  homepage "https://github.com/nova-lang/nova"
  license "MIT"
  version "0.1.0"

  on_macos do
    on_intel do
      url "https://github.com/nova-lang/nova/releases/download/v0.1.0/nova-macos-x86_64.tar.gz"
      sha256 "REPLACE_WITH_RELEASE_SHA256"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/nova-lang/nova/releases/download/v0.1.0/nova-linux-x86_64.tar.gz"
      sha256 "REPLACE_WITH_RELEASE_SHA256"
    end
  end

  def install
    bin.install "nova"

    # `nova_launcher` is a thin wrapper around `nova` that the desktop
    # toolchain uses to set up runtime search paths. Install it if the
    # release tarball ships one; otherwise install a tiny shim.
    if File.exist?("nova_launcher")
      bin.install "nova_launcher"
    else
      (bin/"nova_launcher").write <<~SH
        #!/bin/sh
        exec "#{bin}/nova" "$@"
      SH
      chmod 0755, bin/"nova_launcher"
    end
  end

  test do
    (testpath/"hello.nova").write <<~NOVA
      fn main() {
          println("hello from nova")
      }
      main()
    NOVA
    system "#{bin}/nova", "--version"
  end
end
