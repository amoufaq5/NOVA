"""Nova CLI — run .nova source files."""

import sys
import time

from .lexer import Lexer
from .parser import Parser, ParseError
from .runtime.interpreter import Interpreter, NovaPanic


def run_file(path: str, *, verbose: bool = False):
    try:
        with open(path) as f:
            source = f.read()
    except FileNotFoundError:
        print(f"nova: file not found: {path}", file=sys.stderr)
        return 1

    try:
        t0 = time.time()
        lexer = Lexer(source)
        tokens = lexer.tokenize()
        if verbose:
            print(f"[lexer] {len(tokens)} tokens in {time.time()-t0:.3f}s",
                  file=sys.stderr)

        t1 = time.time()
        parser = Parser(tokens)
        ast = parser.parse()
        if verbose:
            print(f"[parser] {len(ast.body)} top-level nodes in {time.time()-t1:.3f}s",
                  file=sys.stderr)

        t2 = time.time()
        interp = Interpreter()
        result = interp.execute(ast)
        if verbose:
            elapsed = time.time() - t2
            minds = list(interp.minds.keys())
            print(f"[runtime] finished in {elapsed:.3f}s, minds: {minds}",
                  file=sys.stderr)
        return 0

    except ParseError as e:
        print(f"nova: parse error: {e}", file=sys.stderr)
        return 1
    except NovaPanic as e:
        print(f"nova: panic: {e}", file=sys.stderr)
        return 1
    except NameError as e:
        print(f"nova: {e}", file=sys.stderr)
        return 1


def run_repl():
    print("Nova REPL (type 'exit' to quit)")
    interp = Interpreter()
    while True:
        try:
            line = input("nova> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip() in ("exit", "quit"):
            break
        if not line.strip():
            continue
        try:
            lexer = Lexer(line)
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            ast = parser.parse()
            result = interp.execute(ast)
            if result is not None and repr(result) != "none":
                print(repr(result))
        except ParseError as e:
            print(f"parse error: {e}")
        except NovaPanic as e:
            print(f"panic: {e}")
        except NameError as e:
            print(f"{e}")
        except Exception as e:
            print(f"error: {e}")


def main():
    args = sys.argv[1:]
    verbose = False
    files = []

    for arg in args:
        if arg in ("-v", "--verbose"):
            verbose = True
        elif arg in ("-h", "--help"):
            print("Usage: nova [options] <file.nova>")
            print("       nova                    (start REPL)")
            print()
            print("Options:")
            print("  -v, --verbose    Show timing and debug info")
            print("  -h, --help       Show this help")
            print("  --version        Show version")
            return 0
        elif arg == "--version":
            print("Nova 0.1.0")
            return 0
        else:
            files.append(arg)

    if not files:
        run_repl()
        return 0

    for path in files:
        code = run_file(path, verbose=verbose)
        if code != 0:
            return code
    return 0
