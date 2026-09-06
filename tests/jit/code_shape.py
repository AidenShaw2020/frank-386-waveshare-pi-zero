"""Report how many ARM bytes the firmware JIT emits per guest instruction.

Compiles real guest sequences with the ELF's own `nj_compile_v6_trace`, then
disassembles the emitted Thumb.  The point is the *shape* of a block: how much
of it is entry/exit state traffic that native block linking would remove, and
how much is the guest work itself.  Host execution time means nothing here.
"""
import argparse
import struct
from elf_harness import Firmware
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS

# Guest sequences chosen to look like game inner loops, not synthetic opcodes.
CASES = [
    ("reg ALU x6", 32, "01c8" * 6),
    ("byte ALU reg x6", 32, "28c8" * 6),
    ("byte ALU RAM x6", 32, ("2a1d" + "00900000") * 6),
    ("mov reg x6", 32, "89c8" * 6),
    ("16-bit reg ALU x6", 16, "01c8" * 6),
    ("16-bit byte RAM x6", 16, ("2a1e" + "0090") * 6),
    ("lodsb x6", 16, "ac" * 6),
    ("movzx x6", 32, "0fb6c1" * 6),
    ("cbw x6", 32, "98" * 6),
]


def disassemble(code, addr):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    md.detail = False
    return list(md.disasm(code, addr))


def analyse(elf, verbose):
    print(f"{'case':<24}{'guest':>6}{'bytes':>7}{'b/insn':>8}{'ARM insns':>11}")
    rows = []
    for name, bits, encoded in CASES:
        fw = Firmware(elf)
        try:
            code = bytes.fromhex(encoded) + b"\x0f\x0b"
            regs = [0x1000, 0x2000, 0x3000, 0x9000, 0x8000, 0x7000, 0x9000, 0x9100]
            fw.prepare(code, regs, bits, 2)
            block = fw.compile()
            if block is None:
                print(f"{name:<24}{'REJECTED':>32}")
                continue
            ptr, insns = block
            # arm_halfwords is the emitted length; code is the arena pointer.
            blk = fw.call("nj_compile_v6_trace", fw.CPU, fw.START)
            halfwords = struct.unpack(
                "<H", fw.uc.mem_read(blk + fw.layouts["nj_block_t"]["arm_halfwords"], 2))[0]
            nbytes = halfwords * 2
            body = bytes(fw.uc.mem_read(ptr & ~1, nbytes))
            listing = disassemble(body, ptr & ~1)
            rows.append((name, insns, nbytes, nbytes / max(insns, 1), len(listing)))
            print(f"{name:<24}{insns:>6}{nbytes:>7}{nbytes/max(insns,1):>8.1f}{len(listing):>11}")
            if verbose == name or verbose == "all":
                for ins in listing:
                    print(f"    {ins.address:08x}  {ins.bytes.hex():<8} {ins.mnemonic} {ins.op_str}")
        finally:
            fw.close()
    if rows:
        total_insns = sum(r[1] for r in rows)
        total_bytes = sum(r[2] for r in rows)
        print(f"\nmean {total_bytes/total_insns:.1f} bytes per guest instruction "
              f"over {total_insns} compiled instructions")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("elf")
    p.add_argument("--dump", default="", help="case name, or 'all', to disassemble")
    a = p.parse_args()
    analyse(a.elf, a.dump)
