// Reference ARM64 assembly that NOVA's --target=arm64 should emit for
// `fn main() { println("hello from NOVA on ARM64") }` on the iOS side.
//
// Darwin/iOS ARM64 syscall convention:
//   syscall instruction: svc #0x80
//   syscall number reg : x16
//   write syscall #    : 4
//   exit  syscall #    : 1
//
// Assemble:
//   clang -target arm64-apple-ios13.0 -c hello_arm64_ios_reference.s \
//       -o bin/hello_ios.o
// Verify:
//   file bin/hello_ios.o
//   # Mach-O 64-bit arm64 object
//
// This .o file links into an Xcode app bundle via Swift bridging headers.
// The .o by itself does not run as an executable on iOS.

.section __TEXT,__text
.globl _main
.align 2
_main:
    // write(1, msg, 24)
    mov x0, #1                // fd = stdout
    adrp x1, msg@PAGE         // page-aligned addr of msg
    add x1, x1, msg@PAGEOFF
    mov x2, #24               // length
    mov x16, #4               // write syscall (Darwin BSD)
    svc #0x80

    // exit(0)
    mov x0, #0
    mov x16, #1               // exit syscall (Darwin BSD)
    svc #0x80

.section __TEXT,__const
msg:
    .ascii "hello from NOVA on ARM64"
