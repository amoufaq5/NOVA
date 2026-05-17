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

.PHONY: all clean test bootstrap stage1 self-host

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

# Run tests
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

clean:
	rm -rf bin/ /tmp/nova_*.s /tmp/nova_*.o
