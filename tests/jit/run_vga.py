"""Direct native writes to the 0xA0000 aperture.

The shared memory guard used to refuse every physical address in
0xA0000-0xBFFFF. On the DRACIHIS opening that was 1 500 083 refusals per
twenty seconds, all of them writes and every one abandoning the rest of a
compiled block. When the aperture reduces to a plain byte store - chain 4, or
a single-plane mode X write that does nothing to the value - the guard now
hands the block a host pointer instead. These tests drive that decision from
outside, through `njit_vga_write`, on the real ELF's generated code.

Chain 4 is compared against the interpreter: pointing the table at
`phys_mem + 0xA0000` makes both paths write the same bytes, because this
harness installs no iomem callback and the interpreter falls through to plain
physical memory. Mode X cannot be compared that way - its guest addresses are
four bytes apart - so those cases check the destination the emulator's own
formula names, and that nothing lands where a chain-4 store would have.
"""
import argparse
import struct

from elf_harness import Firmware

APERTURE = 0xA0000
ENTRY = 12                  # sizeof(struct njit_vga_write)


def machine(elf, code, regs, table):
    """A prepared firmware with njit_vga_write filled in.

    `table` is a list of (ram, shift, limit) or None, one per operand size.
    """
    m = Firmware(elf)
    m.prepare(code, regs)
    base = m.symbols["njit_vga_write"]
    for i, entry in enumerate(table):
        ram, shift, limit = entry or (0, 0, 0)
        m.uc.mem_write(base + i * ENTRY, struct.pack("<III", ram, shift, limit))
    return m


def linear(m, offset=0):
    """A table whose byte entry writes exactly where the interpreter would."""
    return [(m.RAM + APERTURE + offset, 0, 0x1FFFF), None, None]


def run_block(elf, code, regs, table, label, expect_native):
    jit = machine(elf, code, regs, table)
    try:
        block = jit.compile()
        if block is None:
            raise AssertionError(f"{label}: trace unexpectedly rejected")
        done = jit.call(block[0], jit.CPU, 1)
        nominal = block[1]
        if expect_native and done != nominal:
            raise AssertionError(f"{label}: retired {done} of {nominal}, "
                                 f"the aperture write was refused")
        if not expect_native and done >= nominal:
            raise AssertionError(f"{label}: retired {done} of {nominal}, "
                                 f"the aperture write was NOT refused")
        return jit, done, nominal
    except Exception:
        jit.close()
        raise


def against_interpreter(elf, code, regs, address, label, expect_native=True,
                        table=None):
    """Chain-4 shape: the JIT and the interpreter must agree byte for byte."""
    jit, done, nominal = run_block(elf, code, regs,
                                   table if table is not None
                                   else linear(Firmware(elf)),
                                   label, expect_native)
    reference = Firmware(elf)
    try:
        reference.prepare(code, regs)
        if reference.call("cpu_exec1", reference.CPU, done) != 1:
            raise AssertionError(f"{label}: interpreter fault")
        actual = bytes(jit.uc.mem_read(jit.RAM + address - 4, 16))
        expected = bytes(reference.uc.mem_read(reference.RAM + address - 4, 16))
        if actual != expected:
            raise AssertionError(f"{label}: memory differs at {address:#x}\n"
                                 f"jit={actual.hex()}\nint={expected.hex()}")
        if jit.snapshot() != reference.snapshot():
            raise AssertionError(f"{label}: architectural state differs\n"
                                 f"{jit.snapshot()}\n{reference.snapshot()}")
    finally:
        jit.close()
        reference.close()
    print(f"PASS {label}: retired {done} of {nominal}")


def run(elf):
    # mov eax,ecx, then six byte stores through the same aperture address.
    stores = b"\x89\xc8" + b"\x88\x0e" * 6
    words = b"\x89\xc8" + b"\x89\x0e" * 6
    loads = b"\x89\xc8" + b"\x8a\x0e" * 6
    regs = [0x123480FF, 0x5A, 0, 0, 0x8000, 0, APERTURE, 0]

    probe = Firmware(elf)
    ram = probe.RAM
    probe.close()

    chain4 = [(ram + APERTURE, 0, 0x1FFFF),
              (ram + APERTURE, 0, 0x1FFFE),
              (ram + APERTURE, 0, 0x1FFFC)]

    against_interpreter(elf, stores, regs, APERTURE,
                        "chain-4 byte stores", table=chain4)
    against_interpreter(elf, words, regs, APERTURE,
                        "chain-4 dword stores", table=chain4)

    # An empty table is a mode the audit did not admit, and a build or boot
    # where nothing was published: both must behave as before this existed.
    against_interpreter(elf, stores, regs, APERTURE, "an empty table refuses",
                        expect_native=False, table=[None, None, None])

    # Reads are never direct: a latched mode would have to load s->latch.
    against_interpreter(elf, loads, regs, APERTURE, "loads are never direct",
                        expect_native=False, table=chain4)

    # Past `limit` the interpreter takes over rather than the store running
    # off the end of vga_ram or into the region lent to the JIT.
    over = [0x123480FF, 0x5A, 0, 0, 0x8000, 0, APERTURE + 0x100, 0]
    against_interpreter(elf, stores, over, APERTURE + 0x100,
                        "past the limit refuses", expect_native=False,
                        table=[(ram + APERTURE, 0, 0xFF), None, None])

    # An address below the aperture, admitted by the guard because a wide
    # operand straddles 0xA0000, must not produce a pointer below vga_ram.
    below = [0x123480FF, 0x5A, 0, 0, 0x8000, 0, APERTURE - 2, 0]
    against_interpreter(elf, words, below, APERTURE - 2,
                        "straddling the start refuses", expect_native=False,
                        table=chain4)

    # Mode X: one plane, four-byte stride.  The destination is the emulator's
    # own vga_ram[(offset + bank) * 4 + plane], which is what the table's ram
    # and shift encode; nothing may land at the chain-4 address.
    plane, offset = 3, 0x40
    target = ram + 0x200000                     # a scratch page in guest RAM
    modex = [(target + plane, 2, 0xFFFF), None, None]
    regs_x = [0x123480FF, 0x5A, 0, 0, 0x8000, 0, APERTURE + offset, 0]
    jit, done, nominal = run_block(elf, stores, regs_x, modex,
                                   "mode X byte stores", True)
    try:
        at = target + (offset << 2) + plane
        if bytes(jit.uc.mem_read(at, 1)) != b"\x5a":
            raise AssertionError("mode X store did not reach "
                                 f"{at - ram:#x}")
        if bytes(jit.uc.mem_read(jit.RAM + APERTURE + offset, 1)) != b"\x00":
            raise AssertionError("mode X store also hit the chain-4 address")
    finally:
        jit.close()
    print(f"PASS mode X byte stores: retired {done} of {nominal}")

    # ...and mode X leaves the wider sizes empty, because consecutive guest
    # addresses are four bytes apart and a word store is not a store at all.
    jit, done, nominal = run_block(elf, words, regs_x, modex,
                                   "mode X word stores refuse", False)
    jit.close()
    print(f"PASS mode X word stores refuse: retired {done} of {nominal}")

    print("PASS: direct VGA aperture writes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    run(parser.parse_args().elf)
