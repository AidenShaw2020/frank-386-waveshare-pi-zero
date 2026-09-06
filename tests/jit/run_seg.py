"""Segment loads inside a compiled trace.

`LES`, `MOV Sreg,r/m16` and `MOV r/m16,Sreg` were together 20.7% of the trace
stops measured on the DRACIHIS opening - the largest family after `CALL`. In
real mode and VM86 a segment load is four stores (selector, base = selector
shifted left four, limit 0xffff, flags 0), so the compiler can emit it; what
makes it delicate is that a block bakes `cpu->seg[x].base` into every memory
operand, and a load inside the trace makes that constant wrong for everything
after it.

So these tests do not stop at "the selector arrived". Each one also addresses
memory *through* the segment it just loaded, and compares the whole segment
table and a window of guest memory against the firmware's own interpreter -
which is `set_seg()` itself, the thing being reproduced.
"""
import argparse
import struct

from elf_harness import Firmware

SEG_NAMES = ("es", "cs", "ss", "ds", "fs", "gs", "ldt", "tr")
DATA = 0x3000          # where the tests keep their operands
WINDOW = 0x10000       # a window the tests store into, inside guest RAM


def segments(m):
    """The whole segment table: selector, base, limit and flags each."""
    base = m.CPU + m.layouts["CPUI386"]["seg"]
    raw = bytes(m.uc.mem_read(base, 8 * 16))
    return {SEG_NAMES[i]: struct.unpack_from("<IIII", raw, i * 16)
            for i in range(8)}


def state(m):
    snap = m.snapshot()
    snap["segments"] = segments(m)
    snap["data"] = bytes(m.uc.mem_read(m.RAM + WINDOW, 32))
    return snap


def check(elf, code, regs, operands, count, label, loaded=()):
    """Run the block and the interpreter over the same starting state."""
    jit, reference = Firmware(elf), Firmware(elf)
    try:
        for m in (jit, reference):
            m.prepare(code, regs, bits=16, flags=2)
            for addr, value in operands.items():
                m.uc.mem_write(m.RAM + addr, struct.pack("<H", value))
        block = jit.compile()
        if block is None:
            raise AssertionError(f"{label}: trace unexpectedly rejected")
        ptr, nominal = block
        if nominal != count:
            raise AssertionError(f"{label}: compiled {nominal} of {count} "
                                 f"instructions, so the segment load was "
                                 f"still refused")
        done = jit.call(ptr, jit.CPU, 1)
        if done != count:
            raise AssertionError(f"{label}: retired {done} of {count}")
        if reference.call("cpu_exec1", reference.CPU, done) != 1:
            raise AssertionError(f"{label}: interpreter fault")
        actual, expected = state(jit), state(reference)
        if actual != expected:
            diff = {k: (actual[k], expected[k])
                    for k in actual if actual[k] != expected[k]}
            raise AssertionError(f"{label}: JIT and interpreter differ: {diff}")
        for name in loaded:
            sel, base, limit, flags = actual["segments"][name]
            if base != sel << 4 or limit != 0xFFFF or flags != 0:
                raise AssertionError(
                    f"{label}: {name} = sel {sel:#x} base {base:#x} "
                    f"limit {limit:#x} flags {flags:#x}, not a real-mode load")
    finally:
        jit.close()
        reference.close()
    print(f"PASS {label}: {count} instructions compiled and retired")


def filler(n):
    """n flagless register moves, then a stop the compiler will not admit.

    Without the terminator the compiler keeps decoding into whatever follows
    the test bytes - zeroed RAM is `add [bx+si],al`, which it accepts - and
    the block's length stops describing the test.
    """
    moves = b"\x89\xd0\x89\xd9\x89\xe5\x89\xc2\x89\xcb\x89\xea"
    return moves[:2 * n] + b"\xf4"      # HLT is never admitted


def run(elf):
    # ES is loaded from a register, then addressed through: the store must go
    # to the NEW base, which is the whole point of the dynamic-base path.
    #   0: 89 c8      mov ax,cx
    #   2: 8e c0      mov es,ax
    #   4: 26 88 1d   mov es:[di],bl
    code = b"\x89\xc8\x8e\xc0\x26\x88\x1d" + filler(3)
    regs = [0, WINDOW >> 4, 0x1234, 0x5A, 0x8000, 0x40, 0x50, 0x10]
    check(elf, code, regs, {}, 6, "mov es,ax then a store through es", ("es",))

    # The same from memory, on DS, which is also the default segment for the
    # operand that loads it - so the load has to read through the OLD base and
    # the store that follows through the new one.
    #   0: 8e 1c      mov ds,[si]
    #   2: 88 1d      mov [di],bl
    code = b"\x8e\x1c\x88\x1d" + filler(4)
    regs = [0, 0, 0x1234, 0x5A, 0x8000, 0x40, DATA, 0x20]
    check(elf, code, regs, {DATA: WINDOW >> 4},
          6, "mov ds,[si] then a store through ds", ("ds",))

    # LES: two words, the offset into DI and the selector into ES, then a
    # store through both.
    #   0: c4 3c      les di,[si]
    #   2: 26 88 1d   mov es:[di],bl
    code = b"\xc4\x3c\x26\x88\x1d" + filler(4)
    regs = [0, 0, 0x1234, 0x5A, 0x8000, 0x40, DATA, 0]
    check(elf, code, regs, {DATA: 0x18, DATA + 2: WINDOW >> 4},
          6, "les di,[si] then a store through es", ("es",))

    # LDS, the same shape on the other segment.
    #   0: c5 3c      lds di,[si]
    #   2: 88 1d      mov [di],bl
    code = b"\xc5\x3c\x88\x1d" + filler(4)
    regs = [0, 0, 0x1234, 0x5A, 0x8000, 0x40, DATA, 0]
    check(elf, code, regs, {DATA: 0x1C, DATA + 2: WINDOW >> 4},
          6, "lds di,[si] then a store through ds", ("ds",))

    # Reading a selector back out, both to a register and to memory.
    #   0: 8c c0      mov ax,es
    #   2: 8c d9      mov cx,ds
    #   4: 8c 1c      mov [si],ds
    code = b"\x8c\xc0\x8c\xd9\x8c\x1c" + filler(3)
    regs = [0, 0, 0x1234, 0x5A, 0x8000, 0x40, WINDOW, 0x10]
    check(elf, code, regs, {}, 6, "mov r16,sreg and mov [si],sreg")

    # A segment load followed by a segment override that still names the old
    # segment: DS becomes dynamic, ES does not, and the two operands must not
    # be confused.
    #   0: 8e 1c      mov ds,[si]
    #   2: 26 88 1d   mov es:[di],bl
    #   5: 88 1e ...  mov [disp16],bl
    code = b"\x8e\x1c\x26\x88\x1d\x88\x1e" + struct.pack("<H", 0x30) + \
        filler(3)
    regs = [0, 0, 0x1234, 0x5A, 0x8000, 0x40, DATA, 0x10]
    check(elf, code, regs, {DATA: WINDOW >> 4},
          6, "one segment dynamic, the other still baked", ("ds",))

    # The regression that shipped: a native intra-block link jumping back to
    # a boundary *before* the segment write re-runs operands compiled against
    # the base the write invalidated.  Straight-line tests cannot reach it -
    # it needs a back edge - and on the board it corrupted whatever the guest
    # was reading.  The block must refuse to link and retire one pass.
    #   0: 89 c8      mov ax,cx
    #   2: 26 8a 1d   mov bl,es:[di]     <- addresses through the OLD es
    #   5: 8e c0      mov es,ax          <- and this invalidates it
    #   7: 89 d0      mov dx,ax
    #   9: 29 f0      sub ax,si
    #   b: 75 f3      jnz 0              <- back edge to before the write
    code = bytes.fromhex("89c8" "268a1d" "8ec0" "89d0" "29f0" "75f3")
    regs = [0, WINDOW >> 4, 0x1234, 0x5A, 0x8000, 0x40, 1, 0x20]
    jit = Firmware(elf)
    try:
        jit.prepare(code, regs, bits=16, flags=2)
        block = jit.compile()
        if block is None:
            raise AssertionError("back-edge trace unexpectedly rejected")
        ptr, nominal = block
        linked = jit.block_field(jit.compile_block(), "njl_link", 1)
        if linked:
            raise AssertionError(
                "a back edge to a boundary before the segment write was "
                "linked: those operands would re-run with the new segment")
        jit.put("njl_acc", 0)
        jit.put("njl_pos", 0)
        jit.put("njl_limit", 512)
        done = jit.call(ptr, jit.CPU, 1)
        if done != nominal:
            raise AssertionError(f"retired {done} of {nominal}")
    finally:
        jit.close()
    print(f"PASS back edge across a segment write refuses to link: "
          f"{nominal} instructions")

    print("PASS: segment loads inside a trace")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    run(parser.parse_args().elf)
