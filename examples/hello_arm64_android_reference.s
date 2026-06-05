// Reference ARM64 assembly that NOVA's --target=arm64 should emit for
// `fn main() { println("hello from NOVA on ARM64") }` on the Android side.
//
// Linux/Android ARM64 syscall convention:
//   syscall instruction: svc #0
//   syscall number reg : x8
//   write syscall #    : 64
//   exit  syscall #    : 93
//
// Assemble:
//   clang -target aarch64-linux-android30 -c hello_arm64_android_reference.s \
//       -o bin/hello_android.o
// Verify:
//   file bin/hello_android.o
//   # ELF 64-bit LSB relocatable, ARM aarch64, version 1 (SYSV), not stripped
//
// This .o file links into a JNI .so via NDK; an Android Kotlin/Java wrapper
// loads it via System.loadLibrary. The .o by itself does not run as an
// executable on Android.

.text
.globl _start
_start:
    // write(1, msg, 24)
    mov x0, #1                // fd = stdout
    adrp x1, msg              // page-aligned addr of msg
    add x1, x1, :lo12:msg
    mov x2, #24               // length
    mov x8, #64               // write syscall
    svc #0

    // exit(0)
    mov x0, #0
    mov x8, #93
    svc #0

.data
msg:
    .ascii "hello from NOVA on ARM64"
