# Using tree-sitter-nova in Neovim

This guide assumes you already have Neovim 0.9+ with
[`nvim-treesitter`](https://github.com/nvim-treesitter/nvim-treesitter)
installed.

## Step 1 — register the parser

Add the following to your Neovim config (Lua):

```lua
local parser_config = require("nvim-treesitter.parsers").get_parser_configs()

parser_config.nova = {
  install_info = {
    -- Either a local path:
    url = "~/code/nova/tools/tree-sitter-nova",
    -- or a remote git URL once the grammar is published:
    -- url = "https://github.com/nova-lang/nova",
    -- location = "tools/tree-sitter-nova",
    files = { "src/parser.c" },
    branch = "main",
    generate_requires_npm = false,
    requires_generate_from_grammar = true,
  },
  filetype = "nova",
}

vim.filetype.add({
  extension = {
    nova = "nova",
  },
})
```

## Step 2 — install the parser

Restart Neovim and run:

```vim
:TSInstall nova
```

`nvim-treesitter` will:

1. Clone or copy the grammar directory.
2. Run `tree-sitter generate` to produce `src/parser.c`.
3. Compile `parser.c` into a shared library `nova.so` and drop it into
   `~/.local/share/nvim/site/parser/`.

If you see *"requires_generate_from_grammar = true but tree-sitter CLI
not found"*, install it first:

```bash
npm install -g tree-sitter-cli
```

## Step 3 — install editor queries

`nvim-treesitter` looks for queries under
`runtimepath/queries/<lang>/<query>.scm`. Copy all three bundled
queries (highlights, folds, locals) into your runtime path:

```bash
mkdir -p ~/.config/nvim/queries/nova
cp tools/tree-sitter-nova/queries/highlights.scm ~/.config/nvim/queries/nova/
cp tools/tree-sitter-nova/queries/folds.scm      ~/.config/nvim/queries/nova/
cp tools/tree-sitter-nova/queries/locals.scm     ~/.config/nvim/queries/nova/
```

Alternatively, symlink them for live editing:

```bash
for q in highlights folds locals; do
  ln -sf "$(pwd)/tools/tree-sitter-nova/queries/${q}.scm" \
        ~/.config/nvim/queries/nova/${q}.scm
done
```

Then enable the corresponding modules in your `nvim-treesitter` setup:

```lua
require("nvim-treesitter.configs").setup({
  ensure_installed = { "nova" },
  highlight = { enable = true },
  -- Folding (also requires `set foldmethod=expr` +
  -- `set foldexpr=nvim_treesitter#foldexpr()` in init.vim/init.lua):
  fold      = { enable = true },
  -- Locals (drives goto-definition fallback when no LSP is present):
  locals    = { enable = true },
})
```

## Step 4 — verify

Open any `.nova` file (for example
`examples/hello.nova`) and run:

```vim
:TSPlaygroundToggle
```

You should see the CST in a side panel and the buffer should show
proper syntax colouring (keywords in your `Keyword` group, strings in
`String`, function names in `Function`, etc.).

To confirm the parser is wired:

```vim
:checkhealth nvim-treesitter
```

The output should list `nova` under installed parsers with a green
checkmark.

## Troubleshooting

| Symptom                                       | Fix                                                              |
| --------------------------------------------- | ---------------------------------------------------------------- |
| `Parser not found for language: nova`         | Re-run `:TSInstall nova`; check `:TSInstallInfo`.                |
| `parser_config.nova.install_info.url is nil`  | The `parser_config.nova = { ... }` block isn't sourced before `nvim-treesitter` initialises. Put it before `require("nvim-treesitter.configs").setup({})`. |
| No colours but parser installs                | Queries aren't on `runtimepath`. Verify `:echo &runtimepath`.    |
| `unknown node type` in queries                | Run `:TSUpdate nova` after pulling a newer grammar revision.      |

## Quick test

```bash
$ cat > /tmp/quick.nova <<'EOF'
fn main() {
    println("hello from nvim")
}
main()
EOF

$ nvim /tmp/quick.nova
```

You should see `fn`, `main`, `println` highlighted distinctly from the
string literal.
