"""Nova test runner — discovers and runs test_*.nova / *_test.nova files."""

import os
import sys
import time
import fnmatch

from ..lexer.lexer import Lexer
from ..parser.parser import Parser
from ..runtime.interpreter import Interpreter
from ..runtime.environment import Environment
from ..ast_nodes.nodes import FnDecl, Program
from ..types.values import (
    NovaBuiltin, NovaBool, NovaInt, NovaFloat, NovaStr,
    NovaNone, NovaList, NovaValue, NovaPanic, NovaError,
    ReturnSignal,
)


class AssertionFailed(Exception):
    """Raised when a Nova test assertion fails."""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TestResult:
    """Result of a single test function execution."""

    def __init__(self, name: str, file_path: str):
        self.name = name
        self.file_path = file_path
        self.passed = False
        self.error: str | None = None
        self.duration: float = 0.0

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"{status} {self.name} ({self.duration:.3f}s)"


def _nova_values_equal(a: NovaValue, b: NovaValue) -> bool:
    """Check deep equality of two Nova values for assertion purposes."""
    if type(a) != type(b):
        return False
    if isinstance(a, (NovaInt, NovaFloat, NovaStr, NovaBool)):
        return a.value == b.value
    if isinstance(a, NovaNone):
        return True
    if isinstance(a, NovaList):
        if len(a.elements) != len(b.elements):
            return False
        return all(_nova_values_equal(x, y) for x, y in zip(a.elements, b.elements))
    return a is b


def _nova_value_repr(v: NovaValue) -> str:
    """Readable representation of a Nova value for assertion messages."""
    return repr(v)


def _make_assert_eq():
    """Create the assert_eq(a, b) builtin for tests."""
    def assert_eq(args, kwargs):
        if len(args) < 2:
            raise AssertionFailed("assert_eq requires 2 arguments")
        a, b = args[0], args[1]
        if not _nova_values_equal(a, b):
            msg = f"assert_eq failed: {_nova_value_repr(a)} != {_nova_value_repr(b)}"
            if len(args) >= 3 and isinstance(args[2], NovaStr):
                msg = f"{args[2].value}: {msg}"
            raise AssertionFailed(msg)
        return NovaNone()
    return NovaBuiltin("assert_eq", assert_eq)


def _make_assert_true():
    """Create the assert_true(cond) builtin for tests."""
    def assert_true(args, kwargs):
        if len(args) < 1:
            raise AssertionFailed("assert_true requires 1 argument")
        val = args[0]
        truthy = False
        if isinstance(val, NovaBool):
            truthy = val.value
        elif isinstance(val, NovaNone):
            truthy = False
        elif isinstance(val, NovaInt):
            truthy = val.value != 0
        elif isinstance(val, NovaFloat):
            truthy = val.value != 0.0
        elif isinstance(val, NovaStr):
            truthy = len(val.value) > 0
        elif isinstance(val, NovaList):
            truthy = len(val.elements) > 0
        else:
            truthy = True
        if not truthy:
            msg = f"assert_true failed: {_nova_value_repr(val)} is not truthy"
            if len(args) >= 2 and isinstance(args[1], NovaStr):
                msg = f"{args[1].value}: {msg}"
            raise AssertionFailed(msg)
        return NovaNone()
    return NovaBuiltin("assert_true", assert_true)


def _make_assert_false():
    """Create the assert_false(cond) builtin for tests."""
    def assert_false(args, kwargs):
        if len(args) < 1:
            raise AssertionFailed("assert_false requires 1 argument")
        val = args[0]
        truthy = False
        if isinstance(val, NovaBool):
            truthy = val.value
        elif isinstance(val, NovaNone):
            truthy = False
        elif isinstance(val, NovaInt):
            truthy = val.value != 0
        elif isinstance(val, NovaFloat):
            truthy = val.value != 0.0
        elif isinstance(val, NovaStr):
            truthy = len(val.value) > 0
        elif isinstance(val, NovaList):
            truthy = len(val.elements) > 0
        else:
            truthy = True
        if truthy:
            msg = f"assert_false failed: {_nova_value_repr(val)} is truthy"
            if len(args) >= 2 and isinstance(args[1], NovaStr):
                msg = f"{args[1].value}: {msg}"
            raise AssertionFailed(msg)
        return NovaNone()
    return NovaBuiltin("assert_false", assert_false)


def discover_test_files(path: str) -> list[str]:
    """Find all Nova test files under the given path.

    Matches files named test_*.nova or *_test.nova.
    If path is a single file, returns it if it matches.
    """
    if os.path.isfile(path):
        basename = os.path.basename(path)
        if fnmatch.fnmatch(basename, "test_*.nova") or fnmatch.fnmatch(basename, "*_test.nova"):
            return [path]
        return []

    test_files = []
    for root, dirs, files in os.walk(path):
        for fname in sorted(files):
            if fname.endswith(".nova"):
                if fnmatch.fnmatch(fname, "test_*.nova") or fnmatch.fnmatch(fname, "*_test.nova"):
                    test_files.append(os.path.join(root, fname))
    return sorted(test_files)


def find_test_functions(program: Program) -> list[FnDecl]:
    """Extract all fn test_*() declarations from a parsed program."""
    test_fns = []
    for node in program.body:
        if isinstance(node, FnDecl) and node.name.startswith("test_"):
            test_fns.append(node)
    return test_fns


def run_test_file(file_path: str, verbose: bool = False) -> list[TestResult]:
    """Parse and run all test functions in a single .nova file."""
    results = []

    try:
        with open(file_path, "r") as f:
            source = f.read()
    except OSError as e:
        result = TestResult("(file read)", file_path)
        result.error = f"Cannot read file: {e}"
        return [result]

    # Parse the file
    try:
        tokens = Lexer(source).tokenize()
        program = Parser(tokens).parse()
    except Exception as e:
        result = TestResult("(parse)", file_path)
        result.error = f"Parse error: {e}"
        return [result]

    # Find test functions
    test_fns = find_test_functions(program)
    if not test_fns:
        return []

    # Create a fresh interpreter and register test builtins
    interp = Interpreter()
    interp.global_env.define("assert_eq", _make_assert_eq())
    interp.global_env.define("assert_true", _make_assert_true())
    interp.global_env.define("assert_false", _make_assert_false())

    # Execute top-level code (defines functions, models, etc.) to populate env
    try:
        interp.execute(program)
    except (ReturnSignal, NovaPanic, NovaError, AssertionFailed) as e:
        # Top-level error; report but continue with whatever was defined
        if verbose:
            print(f"  Warning: top-level execution error in {file_path}: {e}")
    except Exception as e:
        if verbose:
            print(f"  Warning: top-level execution error in {file_path}: {e}")

    # Run each test function
    for fn_decl in test_fns:
        result = TestResult(fn_decl.name, file_path)
        start = time.perf_counter()

        try:
            # Look up the function in the environment (it was defined during execute)
            fn_val = interp.global_env.get(fn_decl.name)
            interp._call_function(fn_val, [], {})
            result.passed = True
        except AssertionFailed as e:
            result.error = e.message
        except NovaPanic as e:
            result.error = f"PANIC: {e}"
        except NovaError as e:
            result.error = f"Error: {e}"
        except ReturnSignal:
            # A return from a test function is fine
            result.passed = True
        except Exception as e:
            result.error = f"Internal error: {type(e).__name__}: {e}"

        result.duration = time.perf_counter() - start
        results.append(result)

    return results


def run_tests(path: str, verbose: bool = False) -> int:
    """Discover and run all Nova tests under path. Returns exit code."""
    test_files = discover_test_files(path)

    if not test_files:
        print(f"No test files found in '{path}'")
        print("  (looking for test_*.nova or *_test.nova)")
        return 1

    total_pass = 0
    total_fail = 0
    total_time = 0.0
    all_failures: list[TestResult] = []

    print(f"Discovering tests in '{path}'...")
    print()

    for file_path in test_files:
        rel_path = os.path.relpath(file_path)
        results = run_test_file(file_path, verbose=verbose)

        if not results:
            if verbose:
                print(f"  {rel_path}: no test functions found")
            continue

        print(f"  {rel_path}")
        for r in results:
            total_time += r.duration
            if r.passed:
                total_pass += 1
                print(f"    PASS  {r.name} ({r.duration:.3f}s)")
            else:
                total_fail += 1
                all_failures.append(r)
                print(f"    FAIL  {r.name} ({r.duration:.3f}s)")
                if verbose and r.error:
                    print(f"          {r.error}")

    # Summary
    print()
    print("-" * 60)
    total = total_pass + total_fail
    if total_fail == 0:
        print(f"ALL PASSED: {total_pass} test(s) in {total_time:.3f}s")
    else:
        print(f"FAILED: {total_fail} of {total} test(s) in {total_time:.3f}s")
        print()
        print("Failures:")
        for r in all_failures:
            rel = os.path.relpath(r.file_path)
            print(f"  {rel}::{r.name}")
            if r.error:
                print(f"    {r.error}")

    return 0 if total_fail == 0 else 1
