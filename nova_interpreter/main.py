"""Nova interpreter CLI — 'nova run <file>' or 'nova shell' (REPL)."""

import sys
from .lexer.lexer import Lexer
from .parser.parser import Parser
from .runtime.interpreter import Interpreter


def run_source(source: str, interpreter: Interpreter | None = None) -> None:
    interp = interpreter or Interpreter()
    tokens = Lexer(source).tokenize()
    ast = Parser(tokens).parse()
    interp.execute(ast)


def run_file(path: str) -> None:
    with open(path, "r") as f:
        source = f.read()
    run_source(source)


def repl() -> None:
    print("Nova 0.1.0 — Phase 0 Bootstrap Interpreter")
    print("Type 'exit' to quit.\n")

    interp = Interpreter()
    buffer = ""

    while True:
        try:
            prompt = "nova> " if not buffer else "  ... "
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if line.strip() == "exit":
            break

        buffer += line + "\n"

        # Check if braces are balanced before executing
        opens = buffer.count("{") - buffer.count("}")
        if opens > 0:
            continue

        try:
            run_source(buffer.strip(), interp)
        except Exception as e:
            print(f"Error: {e}")

        buffer = ""


def main():
    if len(sys.argv) < 2:
        repl()
        return

    cmd = sys.argv[1]
    if cmd == "run" and len(sys.argv) >= 3:
        run_file(sys.argv[2])
    elif cmd == "shell":
        repl()
    elif cmd.endswith(".nova"):
        run_file(cmd)
    else:
        print("Usage: nova [run <file.nova> | shell | <file.nova>]")
        sys.exit(1)


if __name__ == "__main__":
    main()
