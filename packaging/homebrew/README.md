# Homebrew packaging

The canonical Homebrew formula for Nova lives at:

```
tools/Formula/nova.rb
```

This directory holds packaging-side helpers that complement the
formula:

- `bump-formula.sh` — non-interactive equivalent of
  `brew bump-formula-pr`. Runs from CI after `release.yml` finishes,
  computes the SHA256 of every tarball uploaded to the release, and
  opens a PR against the `nova-lang/homebrew-nova` tap.

## End-user install

```bash
brew tap nova-lang/nova https://github.com/nova-lang/homebrew-nova
brew install nova
nova --version
```

## Maintainer flow

After a `vX.Y.Z` release has been published:

```bash
# Option A: brew built-in helper (recommended on a Mac).
brew bump-formula-pr nova \
  --url=https://github.com/nova-lang/nova/releases/download/vX.Y.Z/nova-macos-x86_64.tar.gz

# Option B: this script (works on Linux + macOS, requires GH_TOKEN).
VERSION=X.Y.Z packaging/homebrew/bump-formula.sh
```

See `tools/RELEASE.md` for the end-to-end release walkthrough and
`tools/Formula/nova.rb` for the formula itself.
