"""Mixed flag-family sequences: the deferred lazy-flag flush must survive them.

The other suites repeat one instruction six times, so every outstanding flag
record is overwritten by an identical one.  That is the easy case.  These
sequences interleave arithmetic, logical and flagless instructions, memory
operands and early trace termination, which is where a wrongly dropped flush
shows up.  A partial trace is compared against exactly the instructions it
retired, so a compiler that stops early is still checked.
"""
import argparse
import itertools
from elf_harness import Firmware
from run_differential import x86_reference

# kind: 'A' arithmetic (AF defined), 'L' logical (AF undefined), 'N' no flags.
SNIPPETS = [
    ("add eax,ecx", "01c8", "A"),
    ("sub eax,ecx", "29c8", "A"),
    ("cmp eax,ecx", "39c8", "A"),
    ("sub al,cl", "28c8", "A"),
    ("add al,0x7f", "047f", "A"),
    ("add eax,1", "83c001", "A"),
    ("xor eax,ecx", "31c8", "L"),
    ("and eax,ecx", "21c8", "L"),
    ("test eax,ecx", "85c8", "L"),
    ("xor al,cl", "30c8", "L"),
    ("test al,0x55", "a855", "L"),
    ("and eax,0x0f", "83e00f", "L"),
    ("mov eax,ecx", "89c8", "N"),
    ("movzx eax,cl", "0fb6c1", "N"),
    ("cwde", "98", "N"),
    ("sub bl,[0x9000]", "2a1d00900000", "A"),
    ("cmp bl,[0x9000]", "3a1d00900000", "A"),
    ("xor bl,[0x9000]", "321d00900000", "L"),
    ("add eax,[0x9000]", "030500900000", "A"),
    ("inc ebx", "43", "I"),
    ("dec ebx", "4b", "I"),
]

BASE_REGS = [0xFFFFFFF0, 0x56789A03, 0x789ABCDE, 0x9000, 0x8000, 0x4321, 0x9000, 0x9100]
ARITH_MASK, LOGIC_MASK = 0xCD5, 0xCC5


def mask_for(kinds):
    """AF is architecturally undefined after the logical operations.

    INC/DEC ('I') define every arithmetic flag except CF, which they preserve;
    the preserved CF is exactly what a trace must not lose, so it is compared.
    """
    for kind in reversed(kinds):
        if kind == "L":
            return LOGIC_MASK
        if kind in ("A", "I"):
            return ARITH_MASK
    return ARITH_MASK


def compare(elf, parts, regs, flags, memory, label, bits=32, mask=None):
    """Compile, run, and check against whatever prefix the block retired."""
    code = b"".join(bytes.fromhex(p[1]) for p in parts) + b"\x0f\x0b"
    lengths = [len(p[1]) // 2 for p in parts]
    jit, reference = Firmware(elf), Firmware(elf)
    try:
        for machine in (jit, reference):
            machine.prepare(code, list(regs), bits, flags)
            machine.uc.mem_write(machine.RAM + 0x9000, memory)
        block = jit.compile()
        if block is None:
            return None
        done = jit.call(block[0], jit.CPU, 1)
        if not 0 < done <= len(parts):
            raise AssertionError(f"{label}: retired {done} of {len(parts)}")
        actual = jit.snapshot()
        if reference.call("cpu_exec1", reference.CPU, done) != 1:
            raise AssertionError(f"{label}: interpreter fault")
        expected = reference.snapshot()
        retired = sum(lengths[:done])
        independent = x86_reference(code[:retired], regs, flags, bits, done, memory)
        actual["memory"] = bytes(jit.uc.mem_read(jit.RAM + 0x9000, len(memory)))
        expected["memory"] = bytes(reference.uc.mem_read(reference.RAM + 0x9000, len(memory)))
        if mask is None:
            mask = mask_for([p[2] for p in parts[:done]])
        for result in (actual, expected, independent):
            result["flags"] &= mask
        if actual != expected or actual != independent:
            diff = {k: (actual[k], expected[k], independent[k]) for k in actual
                    if actual[k] != expected[k] or actual[k] != independent[k]}
            if "memory" in diff:
                diff["memory"] = [
                    (f"0x{0x9000 + i:x}", actual["memory"][i], expected["memory"][i],
                     independent["memory"][i])
                    for i in range(len(memory))
                    if actual["memory"][i] != expected["memory"][i]
                    or actual["memory"][i] != independent["memory"][i]][:16]
            raise AssertionError(
                f"{label}: retired {done}/{len(parts)}, flags in {flags:08x}\n"
                f"  differences (JIT, interpreter, x86)={diff}")
        return done
    finally:
        jit.close()
        reference.close()


def run(elf):
    memory = bytes(range(256)) * 16
    cases = rejected = partial = 0
    for first, second in itertools.product(SNIPPETS, repeat=2):
        for flags in (2, 0x8D7):
            parts = [first, second] * 3
            label = f"{first[0]} / {second[0]}"
            done = compare(elf, parts, BASE_REGS, flags, memory, label)
            if done is None:
                rejected += 1
            else:
                cases += 1
                partial += done != len(parts)
    print(f"PASS: {cases} interleaved pairs ({partial} retired a partial trace), "
          f"{rejected} rejected outright")

    # Three different flag families in one block, in every order.
    triples = 0
    for combo in itertools.permutations(
            [s for s in SNIPPETS if s[0] in
             ("add eax,ecx", "xor eax,ecx", "mov eax,ecx", "sub bl,[0x9000]")]):
        parts = list(combo) + list(combo)
        if compare(elf, parts, BASE_REGS, 0x8D7, memory, "/".join(p[0] for p in combo)):
            triples += 1
    print(f"PASS: {triples} four-way permutations")


# Stack sequences.  SP sits inside the compared 4 KB window so that every
# pushed and popped byte is checked, not just the registers.
STACK_REGS = [0x123480FF, 0x56789A03, 0x789ABCDE, 0x0F0F0F0F,
              0x9800, 0x4321, 0x9000, 0x9100]
STACK_CASES = {
    "push ax..bp": ["50", "51", "52", "53", "55", "56"],
    "push sp (386 pushes the old SP)": ["54"] * 6,
    "pop ax..si": ["58", "59", "5a", "5b", "5d", "5e"],
    "push/pop alternating": ["50", "58", "51", "59", "52", "5a"],
    "push imm8": ["6a05"] * 6,
    "push imm": ["68"] * 6,          # immediate width filled in per mode
    "alu then push": ["01c8", "50", "31c8", "51", "28c8", "52"],
    "push then alu then pop": ["53", "01c8", "5b", "31c8", "50", "58"],
}


def run_stack(elf, bits):
    """PUSH/POP in a 16-bit stack was gated on VM86; real mode refused it."""
    memory = bytes(range(256)) * 16
    checked = rejected = 0
    for name, encodings in STACK_CASES.items():
        encodings = [e + ("cdab" if bits == 16 else "cdab3412") if e == "68" else e
                     for e in encodings]
        parts = [(name, e, "A" if e in ("01c8", "28c8") else
                  "L" if e == "31c8" else "N") for e in encodings]
        for flags in (2, 0x8D7):
            code = b"".join(bytes.fromhex(p[1]) for p in parts) + b""
            jit = Firmware(elf)
            try:
                jit.prepare(code, list(STACK_REGS), bits, flags)
                accepted = jit.compile() is not None
            finally:
                jit.close()
            if not accepted:
                rejected += 1
                continue
            compare(elf, parts, STACK_REGS, flags, memory, f"{bits}-bit {name}", bits)
            checked += 1
    print(f"PASS: {bits}-bit stack, {checked} sequences verified, {rejected} rejected")


def run_shifts(elf, bits):
    """SHL/SHR/SAR by an immediate; 16-bit forms were 32-bit-only before.

    OF is architecturally defined only for a shift count of one, so it is
    compared there and masked out for the longer counts.  AF is undefined for
    every shift count.
    """
    memory = bytes(range(256)) * 16
    regs = [0x8001F00F, 0x56789A03, 0xFFFF8000, 0x00017FFF,
            0x9800, 0x4321, 0x00008001, 0xFFFFFFFF]
    checked = rejected = 0
    for prefix, note in ((("", "native") if bits == 16 else ("", "native")),
                         ("66", "operand-size prefixed") if bits == 32 else ("", "native")):
        if prefix and bits != 32:
            continue
        for sub, mnemonic in ((4, "shl"), (5, "shr"), (7, "sar")):
            for reg in range(8):
                for count in (1, 2, 4, 7, 15):
                    encoded = (prefix + ("d1" if count == 1 else "c1")
                               + f"{0xC0 | sub << 3 | reg:02x}"
                               + ("" if count == 1 else f"{count:02x}"))
                    parts = [(f"{mnemonic} r{reg},{count}", encoded, "L")] * 6
                    jit = Firmware(elf)
                    try:
                        jit.prepare(b"".join(bytes.fromhex(e[1]) for e in parts)
                                    + b"", list(regs), bits, 0x8D7)
                        accepted = jit.compile() is not None
                    finally:
                        jit.close()
                    if not accepted:
                        rejected += 1
                        continue
                    mask = LOGIC_MASK if count == 1 else LOGIC_MASK & ~0x800
                    compare(elf, parts, regs, 0x8D7, memory,
                            f"{bits}-bit {note} {mnemonic} r{reg} by {count}",
                            bits, mask)
                    checked += 1
    print(f"PASS: {bits}-bit shifts, {checked} verified, {rejected} rejected")


def run_notneg(elf, bits):
    """F6/F7 /2 NOT and /3 NEG; NEG's CF is `cc.dst != 0`, so zero matters."""
    memory = bytes(range(256)) * 16
    checked = rejected = 0
    for regs in ([0, 0x80, 0x8000, 0x80000000, 0x9800, 1, 0x9000, 0xFFFFFFFF],
                 [0x123480FF, 0x56789A03, 0x7FFF8001, 0, 0x9800, 0xFF, 0x9000, 1]):
        for op, width in (("f6", 1), ("f7", 2 if bits == 16 else 4)):
            for sub, mnemonic in ((2, "not"), (3, "neg")):
                for reg in range(8):
                    encoded = op + f"{0xC0 | sub << 3 | reg:02x}"
                    kind = "N" if sub == 2 else "A"
                    parts = [(f"{mnemonic}{width * 8} r{reg}", encoded, kind)] * 6
                    jit = Firmware(elf)
                    try:
                        jit.prepare(bytes.fromhex(encoded) * 6 + b"",
                                    list(regs), bits, 0x8D7)
                        accepted = jit.compile() is not None
                    finally:
                        jit.close()
                    if not accepted:
                        rejected += 1
                        continue
                    compare(elf, parts, regs, 0x8D7, memory,
                            f"{bits}-bit {mnemonic} r{reg}", bits)
                    checked += 1
    print(f"PASS: {bits}-bit NOT/NEG, {checked} verified, {rejected} rejected")


def run_guard_after_flags(elf):
    """A guard exit must publish the flags of the instruction that did retire."""
    checked = 0
    for setter in [s for s in SNIPPETS if s[2] not in ("N", "I") and "[" not in s[0]]:
        for address in (0xA0000, 0xBFFFF, Firmware.RAM_SIZE, 0xFFFFFFFF):
            jit, reference = Firmware(elf), Firmware(elf)
            try:
                # ESI addresses the guarded byte; the setter must retire first.
                code = bytes.fromhex(setter[1]) + b"\x00\x06" * 6 + b"\x0f\x0b"
                regs = list(BASE_REGS)
                regs[6] = address
                for machine in (jit, reference):
                    machine.prepare(code, list(regs), 32, 0x8D7)
                block = jit.compile()
                if block is None:
                    continue
                done = jit.call(block[0], jit.CPU, 1)
                if done != 1:
                    raise AssertionError(f"{setter[0]} then guard: retired {done}, expected 1")
                if reference.call("cpu_exec1", reference.CPU, 1) != 1:
                    raise AssertionError("interpreter fault")
                mask = mask_for([setter[2]])
                actual, expected = jit.snapshot(), reference.snapshot()
                actual["flags"] &= mask
                expected["flags"] &= mask
                if actual != expected:
                    raise AssertionError(
                        f"{setter[0]} then guard at {address:08x}: {actual} != {expected}")
                checked += 1
            finally:
                jit.close()
                reference.close()
    print(f"PASS: {checked} guard side exits after a flag-setting instruction")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    elf = parser.parse_args().elf
    run(elf)
    run_stack(elf, 32)
    run_stack(elf, 16)
    run_shifts(elf, 32)
    run_shifts(elf, 16)
    run_notneg(elf, 32)
    run_notneg(elf, 16)
    run_guard_after_flags(elf)
