"""REP MOVSB trace helper compared with the interpreter and x86.

The helper is deliberately tested with overlapping ranges. REP MOVSB is a
sequence of byte operations, so a forward copy whose destination starts
inside the source propagates bytes; libc memmove semantics are not equivalent.
"""
import argparse
from elf_harness import Firmware
from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_MODE_32
from unicorn.x86_const import (
    UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX,
    UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI,
    UC_X86_REG_EFLAGS, UC_X86_REG_EIP,
)


WINDOW = 0x9000
WINDOW_SIZE = 0x200
FLAG_MASK = 0xCD5


def x86_reference(code, regs, flags, bits, memory):
    """Run through REP completely; Unicorn's instruction count counts its
    individual iterations, so the generic fixed-count reference cannot be
    used for string instructions.
    """
    uc = Uc(UC_ARCH_X86, UC_MODE_16 if bits == 16 else UC_MODE_32)
    uc.mem_map(0, 0x20000)
    uc.mem_write(Firmware.START, code)
    uc.mem_write(WINDOW, memory)
    ids = (UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX,
           UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI)
    for reg, value in zip(ids, regs):
        uc.reg_write(reg, value)
    uc.reg_write(UC_X86_REG_EFLAGS, flags)
    # Stop immediately after MOVSB, before the deliberately-invalid trailer.
    uc.emu_start(Firmware.START, Firmware.START + len(code) - 2)
    return {
        "registers": tuple(uc.reg_read(r) for r in ids),
        "next_ip": uc.reg_read(UC_X86_REG_EIP),
        "flags": uc.reg_read(UC_X86_REG_EFLAGS),
        "memory": bytes(uc.mem_read(WINDOW, len(memory))),
    }


def compare(elf, bits, address_override, si, di, count, flags, label):
    prefix = b"\x67" if address_override else b""
    # Five harmless instructions satisfy the production trace floor; REP is
    # then the sixth and terminal instruction in the compiled prefix.
    code = b"\x89\xdb" * 5 + prefix + b"\xf3\xa4\x0f\x0b"
    regs = [0x12345678, count, 0x89ABCDEF, 0x0BADF00D,
            0x8000, 0x13572468, si, di]
    memory = bytes((i * 37 + 11) & 0xFF for i in range(WINDOW_SIZE))
    jit, reference = Firmware(elf), Firmware(elf)
    try:
        for machine in (jit, reference):
            machine.prepare(code, list(regs), bits, flags)
            machine.uc.mem_write(machine.RAM + WINDOW, memory)

        block = jit.compile()
        if block is None:
            raise AssertionError(f"{label}: trace rejected REP MOVSB")
        ptr, nominal = block
        done = jit.call(ptr, jit.CPU, 1)
        if done != 6 or nominal != 6:
            raise AssertionError(
                f"{label}: retired={done}, compiled={nominal}, expected 6")

        actual = jit.snapshot()
        if reference.call("cpu_exec1", reference.CPU, 6) != 1:
            raise AssertionError(f"{label}: interpreter fault")
        expected = reference.snapshot()
        independent = x86_reference(code, regs, flags, bits, memory)

        for result, machine in ((actual, jit), (expected, reference)):
            result["memory"] = bytes(
                machine.uc.mem_read(machine.RAM + WINDOW, WINDOW_SIZE))
        for result in (actual, expected, independent):
            result["flags"] &= FLAG_MASK

        if actual != expected or actual != independent:
            mismatches = {
                key: (actual[key], expected[key], independent[key])
                for key in actual
                if actual[key] != expected[key] or actual[key] != independent[key]
            }
            if "memory" in mismatches:
                mismatches["memory"] = [
                    (f"0x{WINDOW + i:x}", actual["memory"][i],
                     expected["memory"][i], independent["memory"][i])
                    for i in range(WINDOW_SIZE)
                    if actual["memory"][i] != expected["memory"][i]
                    or actual["memory"][i] != independent["memory"][i]
                ][:16]
            raise AssertionError(
                f"{label}\nflags={flags:08x}, regs={regs}\n"
                f"differences (JIT, interpreter, x86)={mismatches}")
    finally:
        jit.close()
        reference.close()


def address_register(bits, address_override, low):
    address_bits = (16 if bits == 32 else 32) if address_override else bits
    return low if address_bits == 32 else 0xCAFE0000 | low


def run(elf):
    cases = 0
    for bits in (16, 32):
        for address_override in (False, True):
            # The production compiler intentionally keeps 66/67 in 16-bit
            # real mode with the interpreter (the v8.6 startup-safe envelope).
            if bits == 16 and address_override:
                continue
            def reg(low):
                return address_register(bits, address_override, low)

            scenarios = (
                (reg(0x9000), reg(0x9080), 32, 2, "forward non-overlap"),
                (reg(0x9000), reg(0x9001), 16, 2,
                 "forward propagating overlap"),
                (reg(0x9001), reg(0x9000), 16, 2,
                 "forward safe overlap"),
                (reg(0x903F), reg(0x90BF), 32, 2 | 0x400,
                 "backward non-overlap"),
                (reg(0x900F), reg(0x9010), 16, 2 | 0x400,
                 "backward safe overlap"),
                (reg(0x9000), reg(0x9080), 0, 2, "zero count"),
            )
            for si, di, count, flags, name in scenarios:
                mode = "a16" if ((bits == 16) != address_override) else "a32"
                compare(elf, bits, address_override, si, di, count, flags,
                        f"{bits}-bit {mode} {name}")
                cases += 1
    print(f"PASS: {cases} REP MOVSB cases")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    run(parser.parse_args().elf)
