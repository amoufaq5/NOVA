# NOVA Mobile (iOS / Android ARM64) — Audit & Status

## Status (one line)
The toolchain for cross-compiling NOVA programs to iOS Mach-O `.o` and Android
ELF `.o` is **present and working** in this sandbox. NOVA's own ARM64 codegen
(`cg_target == 4`) is **stubbed**: it crashes on any non-trivial input. The
`make smoke-mobile-*` targets ship reference hand-written ARM64 assembly that
demonstrates exactly what NOVA's codegen should emit, and validates both the
ELF and Mach-O cross-link paths end-to-end. Completing the la_lower_function
codegen for ARM64 is the unblocked next step.

## Why mobile is structurally different from desktop

Desktop ports (Windows P12, macOS P1.10, WASM P2.7) all targeted **the same
ISA** (x86-64 or its WASM virtual machine). Mobile is the first port to a
**different ISA**: ARM64 (AArch64). The runtime model also differs:

- **iOS**: apps run sandboxed inside the platform's app process. Bare ELF or
  Mach-O binaries don't run as executables — they ship as `.o` objects that
  Xcode links into a signed `.app` bundle. dyld + Apple Frameworks +
  signing + App Store gate keeping.
- **Android**: similar — bare ELFs don't run. NDK clang produces `.so`
  shared libraries that a Kotlin/Java wrapper loads via
  `System.loadLibrary`. Gradle builds the `.apk` / `.aab`.

So the right deliverable for "mobile native" is not an executable that runs
in this sandbox — it's an **object file** that the platform's app shell
links into its bundle.

## ISA notes (ARM64 / AArch64)

Both iOS and Android-arm64-v8a use the same ARM64 ISA but **different
syscall conventions**:

| Property | Linux / Android | iOS / Darwin |
|---|---|---|
| Syscall instruction | `svc #0` | `svc #0x80` |
| Syscall number reg | `x8` | `x16` |
| write syscall # | `64` | `4` |
| exit syscall # | `93` | `1` |
| read syscall # | `63` | `3` |
| Calling convention | AAPCS64 | AAPCS64 |
| Object format | ELF | Mach-O |
| Entry symbol | `_start` (low-level) / `main` (libc) | `_main` |

Procedure prologue / epilogue is the same on both platforms (the ABI is
AAPCS64):

```
stp x29, x30, [sp, #-16]!    ; save FP+LR
mov x29, sp                  ; set FP
... function body ...
ldp x29, x30, [sp], #16      ; restore FP+LR
ret                          ; return
```

ARM64 immediates have weird encoding (shifted constants). Loading a 32-bit
value typically needs `mov + movk` pairs; loading addresses uses
`adrp + add`. Big literal multiplies are encoded as `mov` + `mul`. NOVA's
codegen-pointer-threshold bug (`var * literal > 2²⁰`, hit 6 times during
P0-P2) likely won't manifest on ARM64 — the bug is in NOVA-source-level
multiplies inside codegen.nova itself, not in emitted assembly.

## The smoke targets

`make smoke-mobile-android` and `make smoke-mobile-ios` ship two
hand-written reference assembly files that produce **valid object files
on the respective targets**:

```sh
make smoke-mobile-android
# -> bin/hello_android.o : ELF 64-bit LSB relocatable, ARM aarch64, version 1 (SYSV)

make smoke-mobile-ios
# -> bin/hello_ios.o     : Mach-O 64-bit arm64 object
```

Both `.o` files contain a minimal `_start` (Android) or `_main` (iOS) that
calls the platform's exit syscall. They don't print anything because the
write-syscall path is different on each platform and the reference files
are deliberately minimal. A `make smoke-mobile-android-run` that loads
the `.o` into an Android emulator is a separate task.

The targets **skip cleanly** if `clang` can't target ARM64 (e.g. when the
LLVM AArch64 backend isn't built):

```
(skip: mobile cross-toolchain not available -- need clang with -target aarch64-linux-android support)
```

## Recommended path

1. **This session (shipped)**: hand-written reference assembly +
   skip-clean Makefile targets + this audit.
2. **Next ~1-2 weeks of NOVA codegen work**: complete `la_lower_function`
   for ARM64 in `src/compiler/codegen.nova`. The macOS / WASM codegen
   functions are good templates — each lowers the same AST through a
   target-specific instruction selector.
3. **Then ~2-4 weeks of platform glue**:
   - iOS: Xcode wrapper Swift app, bridging headers, `lipo` to bundle
     arm64 + x86_64 (simulator) slices.
   - Android: NDK Gradle build, JNI shim, `System.loadLibrary` from
     Kotlin/Java.
4. **Distribution** (~weeks):
   - App Store: signing certificate, provisioning profile, review
     (1-4 week review window).
   - Play Store: simpler — internal track in days, production in 1-2
     weeks.

Wall-clock estimate to "CrossEngin running on an iPhone": **2-3 months**
of focused work. Android is roughly **1-2 months**.

## What this enables

A personal cognitive substrate that lives on the user's phone, not in
the cloud. The privacy story improves dramatically:
- The agent's KG never leaves the device.
- DP at the KG-query layer (P3.6) defends against side-channel leaks.
- Federated learning (P3.7) — souls share statistics, not knowledge.

Combined with the existing audio TTS (P19/P2.6) and visual perception
seam (P3.1), the mobile target sets up "always-with-you" cognitive
agent as a realistic v2.0 trajectory.

## Open gaps (for next session)

- `cg_target == 4` segfaults on real input. The la_init / la_lower /
  la_get_output path needs the actual lowering logic; current code is
  a single-stub exit.
- ARM64 codegen needs separate Linux vs Darwin variants for the
  syscall conventions above. Recommend splitting into `cg_target == 4`
  (Linux ARM64 — Android) and `cg_target == 5` (Darwin ARM64 — iOS).
- No file I/O on ARM64 path yet (read/write/open/close syscalls).
- Multi-arch fat binary support for iOS Simulator (arm64 + x86_64).
- Android NDK CMake/Gradle integration boilerplate.
- iOS Xcode project template with Swift bridging.
