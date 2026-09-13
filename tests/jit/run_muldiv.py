"""Differential MUL/IMUL/DIV/IDIV, interpreter against an independent x86.

The JIT audit in HANDOFF_JIT_2026-09-05.md lists this family as "Not certified
by this audit", and it has had no differential coverage at all.  Flight
Simulator 5 fails with over 1.6 million #UD after taking **exactly one** #DE,
which is what a single wrong divide followed by a mishandled fault looks like -
so this is the gap to close first.

Unlike run_differential.py this does not ask the JIT to compile anything.  The
JIT refuses these opcodes, so requiring a compiled block would only ever report
"unexpectedly rejected"; what matters here is whether the **interpreter** gets
them right, because that is what actually executes them on the board.

Flags: after DIV and IDIV every arithmetic flag is architecturally undefined,
so only the registers are compared.  MUL and IMUL define CF and OF - both set
when the upper half of the product is significant - and leave the rest
undefined, so those two bits are compared and no others.

Divide faults are covered separately at the end: the cases there must raise
#DE, and a divide that quietly returns a wrong answer instead of faulting is
exactly the failure this is looking for.
"""
import argparse
import random

from elf_harness import Firmware
from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_MODE_32, UcError
from unicorn.x86_const import (
    UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX,
    UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI,
    UC_X86_REG_EFLAGS, UC_X86_REG_EIP,
)

IDS = (UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX,
       UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI)

CF_OF = 0x0801          # CF is bit 0, OF is bit 11

# reg field of the ModRM byte
OPS = {"mul": 4, "imul": 5, "div": 6, "idiv": 7}

# Operand registers that are not implicit in these instructions.  EAX and EDX
# are both written by every form, so using them as the source as well would
# test nothing useful and make the expected values ambiguous.
SRC = {1: "ecx", 3: "ebx", 6: "esi", 7: "edi"}


def encode(op, width, rm, bits):
    """F6 /r for 8-bit, F7 /r otherwise, with a size prefix when it differs."""
    modrm = 0xC0 | (OPS[op] << 3) | rm
    prefix = b""
    if width == 8:
        return prefix + bytes([0xF6, modrm])
    if width == 16 and bits == 32:
        prefix = b"\x66"
    elif width == 32 and bits == 16:
        prefix = b"\x66"
    return prefix + bytes([0xF7, modrm])


def reference(code, regs, bits):
    uc = Uc(UC_ARCH_X86, UC_MODE_16 if bits == 16 else UC_MODE_32)
    uc.mem_map(0, 0x20000)
    uc.mem_write(Firmware.START, code)
    for reg, value in zip(IDS, regs):
        uc.reg_write(reg, value)
    uc.reg_write(UC_X86_REG_EFLAGS, 2)
    try:
        uc.emu_start(Firmware.START, Firmware.START + len(code), count=1)
    except UcError:
        return None                     # faulted - #DE
    return {"registers": tuple(uc.reg_read(r) for r in IDS),
            "flags": uc.reg_read(UC_X86_REG_EFLAGS)}


def run_one(elf, code, regs, bits):
    fw = Firmware(elf)
    try:
        fw.prepare(code, regs, bits, 2)
        ok = fw.call("cpu_exec1", fw.CPU, 1)
        if ok != 1:
            return None                 # the interpreter took a fault
        return fw.snapshot()
    finally:
        fw.close()


def operands(op, width, rng):
    """Pick a dividend/multiplier pair that does not fault, for the compare
    cases.  For a divide that means a non-zero divisor whose quotient still
    fits in the destination half - the overflow case is checked separately."""
    limit = (1 << width) - 1
    for _ in range(200):
        src = rng.randint(1, limit)
        if op in ("mul", "imul"):
            return rng.randint(0, limit), src
        wide = rng.randint(0, (1 << (2 * width)) - 1)
        if op == "div":
            if src and wide // src <= limit:
                return wide, src
        else:
            s_src = src - (1 << width) if src >> (width - 1) else src
            if s_src == 0:
                continue
            s_wide = wide - (1 << (2 * width)) if wide >> (2 * width - 1) else wide
            q = int(s_wide / s_src)     # x86 truncates toward zero
            if -(1 << (width - 1)) <= q <= (1 << (width - 1)) - 1:
                return wide, src
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("--rounds", type=int, default=24)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    elf = args.elf
    seed = args.seed if args.seed is not None else random.randrange(1 << 20)
    rng = random.Random(seed)
    total = 0

    for bits in (32, 16):
        for op in ("mul", "imul", "div", "idiv"):
            for width in (8, 16, 32):
                if width == 32 and bits == 16:
                    continue            # not what the guest code under test does
                for rm, name in SRC.items():
                    code = encode(op, width, rm, bits)
                    for _ in range(args.rounds):
                        pair = operands(op, width, rng)
                        if pair is None:
                            continue
                        wide, src = pair
                        regs = [0] * 8
                        if width == 8:
                            regs[0] = wide & 0xFFFF
                        elif width == 16:
                            regs[0] = wide & 0xFFFF
                            regs[2] = (wide >> 16) & 0xFFFF
                        else:
                            regs[0] = wide & 0xFFFFFFFF
                            regs[2] = (wide >> 32) & 0xFFFFFFFF
                        regs[rm] = src
                        want = reference(code, regs, bits)
                        if want is None:
                            continue    # the reference faulted; not a compare case
                        got = run_one(elf, code, regs, bits)
                        label = "%d-bit %s r/m%d,%s" % (bits, op, width, name)
                        if got is None:
                            raise AssertionError(
                                "%s: interpreter faulted where the reference did not\n"
                                "  eax=%08x edx=%08x %s=%08x"
                                % (label, regs[0], regs[2], name, src))
                        mask = CF_OF if op in ("mul", "imul") else 0
                        if (got["registers"] != want["registers"] or
                                (got["flags"] & mask) != (want["flags"] & mask)):
                            raise AssertionError(
                                "%s\n  eax=%08x edx=%08x %s=%08x\n"
                                "  interpreter regs=%s flags=%08x\n"
                                "  x86         regs=%s flags=%08x"
                                % (label, regs[0], regs[2], name, src,
                                   got["registers"], got["flags"] & mask,
                                   want["registers"], want["flags"] & mask))
                        total += 1
                    print("PASS %d-bit %s r/m%d,%s" % (bits, op, width, name),
                          flush=True)

    # Divides that must fault.  A wrong quotient is bad; silently not faulting
    # is worse, because the guest's handler never runs and execution carries on
    # with a corrupt register.
    faults = 0
    for bits in (32, 16):
        for width in (8, 16, 32):
            if width == 32 and bits == 16:
                continue
            for op in ("div", "idiv"):
                for rm in SRC:
                    for wide, src in ((1, 0), ((1 << (2 * width)) - 1, 1)):
                        code = encode(op, width, rm, bits)
                        regs = [0] * 8
                        if width == 8:
                            regs[0] = wide & 0xFFFF
                        elif width == 16:
                            regs[0] = wide & 0xFFFF
                            regs[2] = (wide >> 16) & 0xFFFF
                        else:
                            regs[0] = wide & 0xFFFFFFFF
                            regs[2] = (wide >> 32) & 0xFFFFFFFF
                        regs[rm] = src
                        if reference(code, regs, bits) is not None:
                            continue    # the reference did not fault either
                        if run_one(elf, code, regs, bits) is not None:
                            raise AssertionError(
                                "%d-bit %s r/m%d: no #DE where the reference faults\n"
                                "  eax=%08x edx=%08x divisor=%08x"
                                % (bits, op, width, regs[0], regs[2], src))
                        faults += 1

    print("PASS: %d MUL/IMUL/DIV/IDIV differential cases and %d divide faults; "
          "seed=%d" % (total, faults, seed))


if __name__ == "__main__":
    main()
