# Nova Language Reference

Nova is a self-hosting compiled programming language for x86-64 Linux.
Zero dependencies. No libc. Direct syscalls only.

## Types

### Primitive Types
- **Integers**: 64-bit signed (`let x = 42`)
- **Strings**: Null-terminated, immutable (`let s = "hello"`)
- **Booleans**: `true` (1), `false` (0)
- **None**: `none` (0)
- **Lists**: Dynamic arrays (`let a = list_new()`)

### Literals
- Decimal: `42`, `-7`
- Hex: `0xFF`, `0x1A3`
- Octal: `0o777`
- Binary: `0b1010`
- Float (fixed-point): `3.14` (stored as 3140, scaled by 1000)
- String: `"hello\nworld"`
- List: `[1, 2, 3]`

### String Escape Sequences
- `\n` — newline
- `\t` — tab
- `\\` — backslash
- `\"` — double quote
- `\0` — null byte

## Variables

```nova
let x = 10          // mutable variable
const MAX = 100     // semantic constant (not enforced)
x = 20              // reassignment
```

## Functions

```nova
fn add(a, b) {
    return a + b
}

fn greet(name) {
    println("Hello, " + name + "!")
}
```

Functions are first-class declarations. All parameters are untyped. Functions
can be called before they are declared (forward references are resolved).

## Control Flow

```nova
// If-else
if condition {
    // ...
} else if other {
    // ...
} else {
    // ...
}

// While loop
while condition {
    // ...
    break     // exit loop
    continue  // next iteration
}

// For-in loop (list iteration)
for item in my_list {
    // ...
}

// For-in range loop
for i in range(10) {       // 0 to 9
    // ...
}
for i in range(5, 10) {    // 5 to 9
    // ...
}
```

## Operators

### Arithmetic
| Operator | Description |
|----------|-------------|
| `+` | Addition (integers) or concatenation (strings) |
| `-` | Subtraction |
| `*` | Multiplication |
| `/` | Integer division |
| `%` | Modulo |

### Comparison
| Operator | Description |
|----------|-------------|
| `==` | Equal |
| `!=` | Not equal |
| `<` | Less than |
| `>` | Greater than |
| `<=` | Less than or equal |
| `>=` | Greater than or equal |

### Logical
| Operator | Description |
|----------|-------------|
| `&&` | Short-circuit AND |
| `\|\|` | Short-circuit OR |
| `!` | Logical NOT (unary) |

### Bitwise
| Operator | Description |
|----------|-------------|
| `&` | Bitwise AND |
| `\|` | Bitwise OR |
| `^` | Bitwise XOR |
| `<<` | Left shift |
| `>>` | Right shift |

### Unary
| Operator | Description |
|----------|-------------|
| `-` | Negation |
| `!` | Logical NOT |

## Structs

Structs define named record types with field access via dot notation.

```nova
struct Point {
    x,
    y
}

let p = Point(10, 20)
print_int(p.x)        // field access
p.x = 30              // field assignment
```

Struct instances are constructed by calling the struct name as a function
with positional arguments matching the field order.

## Imports

```nova
import "path/to/file.nova"
```

Imports inline the contents of another Nova source file.

## Index Expressions

Lists support bracket-based indexing:

```nova
let a = [10, 20, 30]
let x = a[0]          // read element
list_set(a, 1, 99)    // write element
```

## Built-in Functions

### I/O
| Function | Description |
|----------|-------------|
| `print(s)` | Print string to stdout |
| `println(s)` | Print string + newline to stdout |
| `print_int(n)` | Print integer to stdout |
| `read_line()` | Read line from stdin |
| `read_file(path)` | Read entire file contents as string |
| `write_file(path, data)` | Write string to file |

### Strings
| Function | Description |
|----------|-------------|
| `len(s)` | String length (also works on lists) |
| `concat(a, b)` | Concatenate two strings (or use `+`) |
| `substr(s, start, length)` | Extract substring |
| `char_at(s, idx)` | Character code (integer) at index |
| `chr(code)` | Convert integer code to 1-character string |
| `int_to_str(n)` | Convert integer to string |
| `str_to_int(s)` | Parse string as integer |
| `starts_with(s, prefix)` | Check if string starts with prefix |
| `ends_with(s, suffix)` | Check if string ends with suffix |
| `str_find(haystack, needle)` | Find substring, returns index or -1 |
| `split(s, delim_char)` | Split string by delimiter character |
| `join(list, sep)` | Join list of strings with separator |
| `hex(n)` | Convert integer to hexadecimal string |

### Lists
| Function | Description |
|----------|-------------|
| `list_new()` | Create empty list |
| `push(list, val)` | Append value to end |
| `pop(list)` | Remove and return last element |
| `len(list)` | Number of elements |
| `list_set(list, idx, val)` | Set element at index |
| `contains(list, val)` | Check if value exists in list |
| `list_remove(list, idx)` | Remove element at index |
| `reverse(list)` | Return new reversed list |
| `sort(list)` | Sort list in place |

### Maps
| Function | Description |
|----------|-------------|
| `map_new()` | Create empty hash map |
| `map_set(map, key, val)` | Set key-value pair |
| `map_get(map, key)` | Get value by key |
| `map_has(map, key)` | Check if key exists |

### Math
| Function | Description |
|----------|-------------|
| `abs(n)` | Absolute value |
| `min(a, b)` | Minimum of two values |
| `max(a, b)` | Maximum of two values |
| `random(max)` | Random integer from 0 to max-1 |
| `random_seed(n)` | Seed the PRNG |

### System
| Function | Description |
|----------|-------------|
| `exit(code)` | Exit process with status code |
| `time()` | Current epoch time in seconds |
| `sleep_ms(ms)` | Sleep for milliseconds |
| `getenv(name)` | Read environment variable |
| `mkdir(path)` | Create directory |
| `unlink(path)` | Delete file |
| `file_size(path)` | File size in bytes |
| `alloc(size)` | Allocate raw memory |

### Network
| Function | Description |
|----------|-------------|
| `socket(domain, type, proto)` | Create socket (e.g., `socket(2, 1, 0)` for TCP) |
| `bind_socket(fd, addr, len)` | Bind socket to address |
| `listen_socket(fd, backlog)` | Listen for connections |
| `accept_conn(fd, addr, len)` | Accept incoming connection |
| `connect_socket(fd, addr, len)` | Connect to remote address |
| `send_data(fd, buf, len)` | Send data on socket |
| `recv_data(fd, buf, len)` | Receive data from socket |
| `close_fd(fd)` | Close file descriptor |
| `make_sockaddr_in(port, ip)` | Create sockaddr_in struct |

### Process
| Function | Description |
|----------|-------------|
| `fork_process()` | Fork current process |
| `waitpid(pid)` | Wait for child process |
| `exec_program(path, argv)` | Replace process with new program |
| `pipe_create()` | Create pipe (returns list `[read_fd, write_fd]`) |

### Debug
| Function | Description |
|----------|-------------|
| `assert(cond, msg)` | Assert condition, exit with message on failure |
| `type_of(val)` | Type tag: 0=null, 1=int, 2=str, 3=list |
| `debug_print(label, val)` | Print label and auto-formatted value |

## Program Structure

A Nova program is a sequence of top-level statements: function declarations,
struct definitions, variable declarations, imports, and expressions. There is
no implicit `main` entry point -- execution begins at the first top-level
statement and proceeds sequentially. By convention, programs define a `main()`
function and call it at the end of the file:

```nova
fn main() {
    println("Hello!")
}

main()
```

## Compilation

Nova compiles to x86-64 assembly, assembled and linked with GNU binutils:

```bash
# Build the compiler
make

# Compile and run a program
make run FILE=examples/hello.nova

# Or manually:
bin/nova myprogram.nova -o /tmp/out.s
as -o /tmp/out.o /tmp/out.s
ld -o /tmp/out /tmp/out.o
/tmp/out
```
