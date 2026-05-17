# Nova Build System
# Builds the self-hosting Nova compiler from source.
#
# Prerequisites: `as` and `ld` (standard GNU binutils)
# No libc, no external dependencies.

AS = as
LD = ld
BOOT = boot/nova_boot
COMPILER_SRC = src/compiler/ast.nova \
               src/compiler/lexer.nova \
               src/compiler/parser.nova \
               src/compiler/codegen.nova \
               src/compiler/compiler.nova

.PHONY: all clean test bootstrap stage1 self-host test-all examples

all: bin/nova

# Step 1: Build the assembly bootstrap
bootstrap: $(BOOT)

$(BOOT): boot/nova_boot.s
	$(AS) -o boot/nova_boot.o boot/nova_boot.s
	$(LD) -o $(BOOT) boot/nova_boot.o

# Step 2: Use bootstrap to compile Nova compiler (stage 1)
bin/nova: $(BOOT) $(COMPILER_SRC)
	@mkdir -p bin
	cat $(COMPILER_SRC) > /tmp/nova_combined.nova
	$(BOOT) /tmp/nova_combined.nova /tmp/nova_combined.nova -o /tmp/nova_stage1.s
	$(AS) -o /tmp/nova_stage1.o /tmp/nova_stage1.s
	$(LD) -o bin/nova /tmp/nova_stage1.o

# Step 3: Verify self-hosting (stage 2 == stage 1)
self-host: bin/nova
	bin/nova /tmp/nova_combined.nova -o /tmp/nova_stage2.s
	diff /tmp/nova_stage1.s /tmp/nova_stage2.s
	@echo "=== SELF-HOSTING VERIFIED ==="

# Compile a .nova file to a binary
%.out: %.nova bin/nova
	bin/nova $< -o /tmp/$*.s
	$(AS) -o /tmp/$*.o /tmp/$*.s
	$(LD) -o $@ /tmp/$*.o

# Run individual tests
test: bin/nova
	@echo "--- test_add ---"
	@bin/nova tests/test_add.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- test_complex ---"
	@bin/nova tests/test_complex.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- test_while ---"
	@bin/nova tests/test_while.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- test_smart_ops ---"
	@bin/nova tests/test_smart_ops.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- test_runtime ---"
	@bin/nova tests/test_runtime.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- All tests passed ---"

# Run all tests using test runner
test-all: bin/nova
	@bash tests/run_tests.sh

# Build and run all examples
examples: bin/nova
	@echo "=== Building Examples ==="
	@for f in examples/*.nova; do \
		name=$$(basename "$$f" .nova); \
		echo "--- $$name ---"; \
		bin/nova "$$f" -o /tmp/ex_$$name.s 2>/dev/null && \
		$(AS) -o /tmp/ex_$$name.o /tmp/ex_$$name.s 2>/dev/null && \
		$(LD) -o /tmp/ex_$$name /tmp/ex_$$name.o 2>/dev/null && \
		timeout 5 /tmp/ex_$$name 2>/dev/null; \
		echo ""; \
	done

# Compile a single .nova file and run it
run: bin/nova
	@if [ -z "$(FILE)" ]; then echo "Usage: make run FILE=path/to/file.nova"; exit 1; fi
	@bin/nova $(FILE) -o /tmp/nova_run.s && \
	$(AS) -o /tmp/nova_run.o /tmp/nova_run.s && \
	$(LD) -o /tmp/nova_run /tmp/nova_run.o && \
	/tmp/nova_run

# Show compiler stats
stats:
	@echo "=== Nova Codebase Stats ==="
	@echo -n "Bootstrap assembly: "; wc -l boot/nova_boot.s | awk '{print $$1 " lines"}'
	@echo -n "Compiler (Nova):    "; cat $(COMPILER_SRC) | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Core types:         "; cat src/core/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Mind systems:       "; cat src/mind/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Runtime:            "; cat src/runtime/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Tests:              "; cat tests/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Examples:           "; cat examples/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Total Nova:         "; find . -name "*.nova" | xargs cat | wc -l | awk '{print $$0 " lines"}'

clean:
	rm -rf bin/ /tmp/nova_*.s /tmp/nova_*.o /tmp/t /tmp/t.s /tmp/t.o /tmp/ex_*
