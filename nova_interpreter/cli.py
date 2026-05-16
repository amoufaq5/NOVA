"""Nova CLI — unified command-line interface for the Nova language.

Subcommands:
    nova run <file.nova>    Run a Nova source file
    nova test [path]        Discover and run Nova test files
    nova shell              Launch the interactive REPL
    nova fmt <file.nova>    Format a Nova source file
    nova lint <file.nova>   Lint a Nova source file
    nova version            Print version info
"""

import argparse
import sys
import os

__version__ = "0.1.0"


def cmd_run(args):
    """Run a Nova source file."""
    from .main import run_file
    try:
        run_file(args.file)
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_test(args):
    """Discover and run Nova test files."""
    from .tools.test_runner import run_tests
    path = args.path or "."
    exit_code = run_tests(path, verbose=args.verbose)
    sys.exit(exit_code)


def cmd_shell(args):
    """Launch the interactive REPL."""
    from .main import repl
    repl()


def cmd_fmt(args):
    """Format a Nova source file."""
    from .tools.formatter import format_file
    try:
        formatted = format_file(args.file)
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.write:
        with open(args.file, "w") as f:
            f.write(formatted)
        print(f"Formatted {args.file}")
    elif args.check:
        with open(args.file, "r") as f:
            original = f.read()
        if original != formatted:
            print(f"{args.file}: needs formatting")
            sys.exit(1)
        else:
            print(f"{args.file}: OK")
    else:
        # Print to stdout (default)
        print(formatted, end="")


def cmd_lint(args):
    """Lint a Nova source file."""
    from .tools.linter import lint_file
    try:
        warnings = lint_file(args.file)
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not warnings:
        print(f"{args.file}: no warnings")
        return

    rel_path = os.path.relpath(args.file)
    for w in warnings:
        print(f"{rel_path}:{w.line}:{w.col}: [{w.code}] {w.message}")

    print(f"\n{len(warnings)} warning(s) in {rel_path}")
    sys.exit(1)


def cmd_version(args):
    """Print version info."""
    print(f"Nova {__version__} — Phase 0 Bootstrap Interpreter")
    print(f"Python {sys.version.split()[0]}")


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="nova",
        description="Nova programming language CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- run ---
    p_run = subparsers.add_parser("run", help="Run a Nova source file")
    p_run.add_argument("file", help="Path to .nova file")
    p_run.set_defaults(func=cmd_run)

    # --- test ---
    p_test = subparsers.add_parser("test", help="Discover and run Nova test files")
    p_test.add_argument("path", nargs="?", default=".",
                        help="Directory or file to search for tests (default: .)")
    p_test.add_argument("-v", "--verbose", action="store_true",
                        help="Show detailed output for failures")
    p_test.set_defaults(func=cmd_test)

    # --- shell ---
    p_shell = subparsers.add_parser("shell", help="Launch the interactive REPL")
    p_shell.set_defaults(func=cmd_shell)

    # --- fmt ---
    p_fmt = subparsers.add_parser("fmt", help="Format a Nova source file")
    p_fmt.add_argument("file", help="Path to .nova file")
    p_fmt.add_argument("-w", "--write", action="store_true",
                       help="Write formatted output back to file")
    p_fmt.add_argument("--check", action="store_true",
                       help="Check if file is formatted (exit 1 if not)")
    p_fmt.set_defaults(func=cmd_fmt)

    # --- lint ---
    p_lint = subparsers.add_parser("lint", help="Lint a Nova source file")
    p_lint.add_argument("file", help="Path to .nova file")
    p_lint.set_defaults(func=cmd_lint)

    # --- version ---
    p_version = subparsers.add_parser("version", help="Print version info")
    p_version.set_defaults(func=cmd_version)

    return parser


def main():
    """CLI entry point."""
    # Handle legacy usage: `nova <file.nova>` as shorthand for `nova run <file.nova>`
    if len(sys.argv) >= 2 and sys.argv[1].endswith(".nova"):
        from .main import run_file
        try:
            run_file(sys.argv[1])
        except FileNotFoundError:
            print(f"Error: file not found: {sys.argv[1]}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)
