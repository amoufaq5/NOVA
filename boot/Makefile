AS = as
LD = ld
ASFLAGS =
LDFLAGS =

nova_boot: nova_boot.o
	$(LD) $(LDFLAGS) -o $@ $<

nova_boot.o: nova_boot.s
	$(AS) $(ASFLAGS) -o $@ $<

clean:
	rm -f nova_boot.o nova_boot

.PHONY: clean
