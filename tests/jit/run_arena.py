"""The JIT must still work after its arena moves into the framebuffer.

`njit_vga_arena_offer` lends the top of gfx_buffer to the code arena when the
small permanent one fills up (see src/njit_vga_arena.h).  Moving the arena
invalidates every block, so the interesting question is not whether the switch
happens but whether the code emitted afterwards - at a completely different
address, reached through the same absolute-address trampolines - still
executes correctly.  This compiles into both arenas and checks the results
against the interpreter and an independent x86 engine.
"""
import argparse
import struct
from elf_harness import Firmware
from run_differential import x86_reference

# Big blocks, so the 8 KB arena is exhausted in a handful of compiles.
UNIT = "8b06" "46"                       # mov eax,[esi] / inc esi
REGS = [0x1000, 0x2000, 0x3000, 0x9000, 0x8000, 0x7000, 0x9000, 0x9100]
LENT_BASE, LENT_LEN = 0x20050000, 64 * 1024


def block_at(fw, ip):
    """Compile at `ip` and return (code pointer, guest instructions)."""
    blk = fw.call("nj_compile_v6_trace", fw.CPU, ip)
    if not blk:
        return None, 0
    layout = fw.layouts["nj_block_t"]
    return fw.read32(blk + layout["code"]), fw.uc.mem_read(blk + layout["insns"], 1)[0]


def run(elf):
    fw = Firmware(elf)
    try:
        # A long run of the same two instructions, so every start offset is a
        # valid instruction boundary and every block is a distinct address.
        body = bytes.fromhex(UNIT)
        fw.prepare(body * 1024, list(REGS), 32, 2)
        fw.call("nj_guards_refresh", fw.CPU)

        # Before the offer, everything must come out of the permanent arena.
        first, _ = block_at(fw, fw.START)
        if first is None:
            raise AssertionError("the fill sequence does not compile at all")
        if LENT_BASE <= first < LENT_BASE + LENT_LEN:
            raise AssertionError("compiled into the lent region before it was offered")

        fw.call("njit_vga_arena_offer", LENT_BASE, LENT_LEN)
        lent = permanent = 0
        for i in range(64):
            ptr, _ = block_at(fw, fw.START + i * len(body))
            if ptr is None:
                continue
            if LENT_BASE <= ptr < LENT_BASE + LENT_LEN:
                lent += 1
            else:
                permanent += 1
        if not lent:
            raise AssertionError(f"the arena never moved: {permanent} blocks, all permanent")
        print(f"PASS: arena moved into the lent region after {permanent} blocks")

        # The point of the test: code generated in the new arena still runs.
        check(elf, LENT_BASE, LENT_LEN, borrow=True)
        print("PASS: a block compiled in the lent region matches the interpreter")

        # And giving it back returns the arena without breaking anything.
        check(elf, LENT_BASE, LENT_LEN, borrow=True, release=True)
        print("PASS: after release the arena is permanent again and still correct")
    finally:
        fw.close()


def check(elf, base, length, borrow, release=False):
    """Compile and run a real sequence with the arena in the given state."""
    code = bytes.fromhex("01c8") * 6 + b"\x0f\x0b"
    jit, reference = Firmware(elf), Firmware(elf)
    try:
        for machine in (jit, reference):
            machine.prepare(code, list(REGS), 32, 2)
        if borrow:
            # The compilers assume the dispatcher has built the trampolines.
            jit.call("nj_guards_refresh", jit.CPU)
            jit.call("njit_vga_arena_offer", base, length)
            # Fill the permanent arena so the next compile has to borrow.
            filler = Firmware.START + 0x800
            jit.uc.mem_write(jit.RAM + filler, bytes.fromhex(UNIT) * 512)
            for i in range(64):
                jit.call("nj_compile_v6_trace", jit.CPU, filler + i * 3)
        if release:
            jit.call("njit_vga_arena_release")
        block = jit.compile()
        if block is None:
            raise AssertionError("rejected after the arena move")
        ptr, _ = block
        inside = base <= (ptr & ~1) < base + length
        if borrow and not release and not inside:
            raise AssertionError(f"expected a lent-region block, got {ptr:#x}")
        if release and inside:
            raise AssertionError(f"still emitting into the lent region at {ptr:#x}")
        if jit.call(ptr, jit.CPU, 1) != 6:
            raise AssertionError("the relocated block did not retire six instructions")
        actual = jit.snapshot()
        reference.call("cpu_exec1", reference.CPU, 6)
        expected = reference.snapshot()
        independent = x86_reference(code, REGS, 2, 32, 6)
        for result in (actual, expected, independent):
            result["flags"] &= 0xCD5
        if actual != expected or actual != independent:
            raise AssertionError(f"mismatch after the arena move: {actual} {expected} {independent}")
    finally:
        jit.close()
        reference.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    run(parser.parse_args().elf)
