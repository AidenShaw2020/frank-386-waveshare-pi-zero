"""Which x86 forms does the trace compiler actually admit?

The handoff's family table was assembled by reading the emitter.  This asks
the emitter instead: each entry is compiled as six identical instructions by
the ELF's own `nj_compile_v6_trace`, and the result is how many it accepted.
Six is the trace admission floor for 16-bit mode, so a full row means the
whole run compiled.  Accepted rows also report emitted bytes per guest
instruction, which is what the 8 KB arena is actually spent on.

This measures admission, not correctness; run_differential.py does the latter.
"""
import argparse
import struct
from collections import OrderedDict
from elf_harness import Firmware

M32, M16 = 0x9000 .to_bytes(4, "little").hex(), 0x9000 .to_bytes(2, "little").hex()
# ModR/M for "absolute address": mod=00 rm=101 (32-bit) or rm=110 (16-bit).
def absolute(reg, bits):
    return f"{reg << 3 | (5 if bits == 32 else 6):02x}" + (M32 if bits == 32 else M16)

def cases(bits):
    a = lambda reg: absolute(reg, bits)
    return OrderedDict([
        ("MOV r,r (89)", "89c8"),
        ("MOV r,r8 (88)", "88c8"),
        ("MOV r,m (8b)", "8b" + a(0)),
        ("MOV m,r (89)", "89" + a(0)),
        ("MOV r8,m (8a)", "8a" + a(0)),
        ("MOV m,imm (c7)", "c7" + a(0) + ("0011" if bits == 16 else "00110000")),
        ("MOV r,imm (b8)", "b8" + ("3412" if bits == 16 else "12345678")),
        ("LEA (8d)", "8d" + a(0)),
        ("XCHG r,r (87)", "87c8"),
        ("XCHG eAX,r (91)", "91"),
        ("ADD r,r (01)", "01c8"),
        ("ADD r,m (03)", "03" + a(0)),
        ("ADD m,r (01)", "01" + a(0)),
        ("ADC r,r (11)", "11c8"),
        ("SBB r,r (19)", "19c8"),
        ("ALU imm8 (83 /0)", "83c001"),
        ("ALU imm (81 /5)", "81e8" + ("3412" if bits == 16 else "12345678")),
        ("ALU acc imm (05)", "05" + ("3412" if bits == 16 else "12345678")),
        ("INC r (40)", "40"),
        ("DEC r (48)", "48"),
        ("INC m (ff /0)", "ff" + a(0)),
        ("NEG r (f7 /3)", "f7d8"),
        ("NOT r (f7 /2)", "f7d0"),
        ("MUL r (f7 /4)", "f7e1"),
        ("IMUL r (f7 /5)", "f7e9"),
        ("IMUL r,r (0faf)", "0fafc1"),
        ("DIV r (f7 /6)", "f7f1"),
        ("IDIV r (f7 /7)", "f7f9"),
        ("SHL r,1 (d1 /4)", "d1e0"),
        ("SHL r,imm (c1 /4)", "c1e004"),
        ("SHL r,CL (d3 /4)", "d3e0"),
        ("SHR r,imm (c1 /5)", "c1e804"),
        ("SAR r,imm (c1 /7)", "c1f804"),
        ("ROL r,imm (c1 /0)", "c1c004"),
        ("SHLD (0fa4)", "0fa4c804"),
        ("SHRD (0fac)", "0facc804"),
        ("TEST r,r (85)", "85c8"),
        ("CMP r,m (3b)", "3b" + a(0)),
        ("Jcc not taken (75)", "7500"),
        ("SETcc (0f95)", "0f95c0"),
        ("MOVZX r,r8 (0fb6)", "0fb6c1"),
        ("MOVZX r,m8 (0fb6)", "0fb6" + a(0)),
        ("MOVSX r,r8 (0fbe)", "0fbec1"),
        ("MOVSX r,r16 (0fbf)", "0fbfc1"),
        ("CBW/CWDE (98)", "98"),
        ("CWD/CDQ (99)", "99"),
        ("PUSH r (50)", "50"),
        ("POP r (58)", "58"),
        ("PUSH imm (68)", "68" + ("3412" if bits == 16 else "12345678")),
        ("PUSHF (9c)", "9c"),
        ("POPF (9d)", "9d"),
        ("LEAVE (c9)", "c9"),
        ("ENTER (c8)", "c8000000"),
        ("BT r,r (0fa3)", "0fa3c8"),
        ("BTS r,r (0fab)", "0fabc8"),
        ("BSF r,r (0fbc)", "0fbcc1"),
        ("BSR r,r (0fbd)", "0fbdc1"),
        ("LODSB (ac)", "ac"),
        ("LODSW/D (ad)", "ad"),
        ("STOSB (aa)", "aa"),
        ("MOVSB (a4)", "a4"),
        ("SCASB (ae)", "ae"),
        ("CMPSB (a6)", "a6"),
        ("REP MOVSB (f3a4)", "f3a4"),
        ("REP STOSW/D (f3ab)", "f3ab"),
        ("CLC (f8)", "f8"),
        ("STC (f9)", "f9"),
        ("CLD (fc)", "fc"),
        ("STD (fd)", "fd"),
        ("SAHF (9e)", "9e"),
        ("LAHF (9f)", "9f"),
        ("XLAT (d7)", "d7"),
        ("DAA (27)", "27"),
        ("AAM (d4)", "d40a"),
        ("NOP (90)", "90"),
        ("IN al,dx (ec)", "ec"),
        ("OUT dx,al (ee)", "ee"),
    ])


def probe(elf, bits):
    accepted = []
    rejected = []
    for name, encoded in cases(bits).items():
        fw = Firmware(elf)
        try:
            body = bytes.fromhex(encoded)
            fw.prepare(body * 6 + b"\x0f\x0b",
                       [0x1000, 0x2000, 0x3000, 0x9000, 0x8000, 0x7000, 0x9000, 0x9100],
                       bits, 2)
            block = fw.compile()
            if block is None:
                rejected.append(name)
                continue
            ptr, insns = block
            blk = fw.call("nj_compile_v6_trace", fw.CPU, fw.START)
            halfwords = struct.unpack(
                "<H", fw.uc.mem_read(blk + fw.layouts["nj_block_t"]["arm_halfwords"], 2))[0]
            accepted.append((name, insns, halfwords * 2 / max(insns, 1)))
        except Exception as exc:                       # harness faults are data too
            rejected.append(f"{name} [{type(exc).__name__}]")
        finally:
            fw.close()
    return accepted, rejected


def main(elf):
    for bits in (32, 16):
        accepted, rejected = probe(elf, bits)
        total = len(accepted) + len(rejected)
        print(f"\n=== {bits}-bit: {len(accepted)} of {total} forms admitted ===")
        for name, insns, density in sorted(accepted, key=lambda r: -r[2]):
            note = "" if insns == 6 else f"  (only {insns}/6)"
            print(f"  ok    {name:<26}{density:6.1f} B/insn{note}")
        for name in rejected:
            print(f"  NO    {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    main(parser.parse_args().elf)
