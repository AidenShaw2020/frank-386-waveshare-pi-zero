"""Initial straight-line register suite; failures are never counted as skips."""
import argparse
import random
from elf_harness import Firmware
from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_MODE_32
from unicorn.x86_const import (
    UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX,
    UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI,
    UC_X86_REG_EFLAGS, UC_X86_REG_EIP,
)


def x86_reference(code, regs, flags, bits, count, memory=None):
    """Independent x86 engine: interpreter agreement alone is not a proof."""
    uc = Uc(UC_ARCH_X86, UC_MODE_16 if bits == 16 else UC_MODE_32)
    uc.mem_map(0, 0x20000)
    uc.mem_write(Firmware.START, code)
    if memory is not None:
        uc.mem_write(0x9000, memory)
    ids = (UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_EBX,
           UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EDI)
    for reg, value in zip(ids, regs):
        uc.reg_write(reg, value)
    uc.reg_write(UC_X86_REG_EFLAGS, flags)
    uc.emu_start(Firmware.START, Firmware.START + len(code), count=count)
    result = {"registers": tuple(uc.reg_read(r) for r in ids),
              "next_ip": uc.reg_read(UC_X86_REG_EIP), "flags": uc.reg_read(UC_X86_REG_EFLAGS)}
    if memory is not None:
        result["memory"] = bytes(uc.mem_read(0x9000, len(memory)))
    return result


def check(elf, code, regs, flags, bits, count, mask, label, memory=None):
    jit, reference = Firmware(elf), Firmware(elf)
    try:
        for machine in (jit, reference):
            machine.prepare(code, regs, bits, flags)
            if memory is not None:
                machine.uc.mem_write(machine.RAM + 0x9000, memory)
        block = jit.compile()
        if block is None:
            raise AssertionError(f"{label}: unexpectedly rejected")
        ptr, nominal = block
        done = jit.call(ptr, jit.CPU, 1)
        if done != count or nominal != count:
            raise AssertionError(f"{label}: retired {done}, compiled {nominal}, expected {count}")
        actual = jit.snapshot()
        if reference.call("cpu_exec1", reference.CPU, done) != 1:
            raise AssertionError(f"{label}: interpreter fault")
        expected = reference.snapshot()
        independent = x86_reference(code, regs, flags, bits, done, memory)
        if memory is not None:
            actual["memory"] = bytes(jit.uc.mem_read(jit.RAM + 0x9000, len(memory)))
            expected["memory"] = bytes(reference.uc.mem_read(reference.RAM + 0x9000, len(memory)))
        for result in (actual, expected, independent):
            result["flags"] &= mask
        if actual != expected or actual != independent:
            mismatches = {key: (actual[key], expected[key], independent[key])
                          for key in actual if actual[key] != expected[key] or actual[key] != independent[key]}
            raise AssertionError(f"{label}\ninitial={regs}, flags={flags:08x}\n"
                                 f"differences (JIT, interpreter, x86)={mismatches}")
    finally:
        jit.close()
        reference.close()


def run(elf, seed, rounds):
    rng = random.Random(seed)
    # Mask only architecturally defined arithmetic flags for each operation.
    cases = [
        ("mov eax,ecx", "89c8", 0xCD5),
        ("mov al,ah", "88e0", 0xCD5),
        ("mov ah,cl", "88cc", 0xCD5),
        ("add eax,ecx", "01c8", 0xCD5),
        ("sub eax,ecx", "29c8", 0xCD5),
        ("xor eax,ecx", "31c8", 0xCC5),
        ("cmp eax,ecx", "39c8", 0xCD5),
        ("test eax,ecx", "85c8", 0xCC5),
        ("add al,imm8", "047f", 0xCD5),
        ("sub al,imm8", "2c81", 0xCD5),
        ("movzx ax/eax,cl", "0fb6c1", 0xCD5),
        ("movsx ax/eax,ch", "0fbec5", 0xCD5),
        ("cbw/cwde", "98", 0xCD5),
        ("cwd/cdq", "99", 0xCD5),
        ("add then cbw/cwde (lazy flags)", "01c898", 0xCD5),
        ("sub then cwd/cdq (lazy flags)", "29c899", 0xCD5),
    ]
    count = 0
    for bits in (32, 16):
        for name, encoded, mask in cases:
            for iteration in range(rounds):
                regs = [rng.getrandbits(32) for _ in range(8)]
                regs[4] = 0x8000
                flags = 2 | (rng.getrandbits(12) & 0xCD5)
                # Six instructions clear the real-mode trace admission floor.
                code = bytes.fromhex(encoded) * 6 + b"\x0f\x0b"
                expected_count = 12 if "lazy flags" in name else 6
                if bits == 16 and name == "sub eax,ecx" and iteration == 0:
                    # Original AF mismatch found before the BFI fix.
                    regs[0], regs[1] = 414477685, 3221989613
                check(elf, code, regs, flags, bits, expected_count, mask,
                      f"{bits}-bit {name} seed={seed} iteration={iteration}")
                count += 1
            print(f"PASS {bits}-bit {name}: {rounds} states", flush=True)
    # Accumulator forms with an immediate. Their immediate is as wide as the
    # operand, so unlike the table above they cannot share one encoding
    # between the two modes and need their own loop. ADC and SBB are absent
    # on purpose: they read CF, which the trace compiler may still be holding
    # lazily, and they stay with the interpreter.
    for bits in (32, 16):
        width = 4 if bits == 32 else 2
        limit = (1 << (8 * width)) - 1
        for op, name, mask in ((0x05, "add", 0xCD5), (0x0D, "or", 0xCC5),
                               (0x25, "and", 0xCC5), (0x2D, "sub", 0xCD5),
                               (0x35, "xor", 0xCC5), (0x3D, "cmp", 0xCD5),
                               (0xA9, "test", 0xCC5)):
            for imm in (1, 0x7FFF, 0x8000, limit):
                for iteration in range(max(rounds // 4, 1)):
                    regs = [rng.getrandbits(32) for _ in range(8)]
                    regs[4] = 0x8000
                    flags = 2 | (rng.getrandbits(12) & 0xCD5)
                    instruction = bytes((op,)) + (imm & limit).to_bytes(width, "little")
                    code = instruction * 6 + bytes.fromhex("0f0b")
                    check(elf, code, regs, flags, bits, 6, mask,
                          f"{bits}-bit {name} acc,{imm:#x} seed={seed} "
                          f"iteration={iteration}")
                    count += 1
            print(f"PASS {bits}-bit {name} accumulator immediate", flush=True)

    # Shifts by an immediate, every width the trace compiler emits.
    #
    # The 8-bit forms are here because D0 - shift a byte by one - was ending
    # 83% of Tyrian 2000's traces, and a new emitter for a flag-setting
    # instruction is exactly what this suite exists to check.  AF is masked
    # out because x86 leaves it undefined for shifts, and OF as well whenever
    # the count is not one, for the same reason.
    for bits in (32, 16):
        for op, byte_op, has_imm in ((0xD0, True, False), (0xC0, True, True),
                                     (0xD1, False, False), (0xC1, False, True)):
            for sub, name in ((4, "shl"), (5, "shr"), (7, "sar")):
                counts = (1,) if not has_imm else (1, 2, 3, 7)
                for reg in range(8):
                    for cnt in counts:
                        if byte_op and cnt >= 8:
                            continue
                        modrm = 0xC0 | (sub << 3) | reg
                        instruction = bytes((op, modrm))
                        if has_imm:
                            instruction += bytes((cnt,))
                        mask = 0xCC5 if cnt == 1 else (0xCC5 & ~0x800)
                        regs = [rng.getrandbits(32) for _ in range(8)]
                        regs[4] = 0x8000
                        flags = 2 | (rng.getrandbits(12) & 0xCD5)
                        check(elf, instruction * 6 + b"", regs, flags,
                              bits, 6, mask,
                              f"{bits}-bit {name} {'byte' if byte_op else 'word'} "
                              f"reg={reg} count={cnt} seed={seed}")
                        count += 1
            print(f"PASS {bits}-bit shifts opcode={op:02x}", flush=True)

    # FE /0 and /1 - INC and DEC on a byte register.  They preserve CF and
    # leave the other five flags to the lazy record, so the incoming CF is
    # varied deliberately by the random flags above.
    for bits in (32, 16):
        for sub, name in ((0, "inc"), (1, "dec")):
            for reg in range(8):
                for iteration in range(max(rounds // 2, 1)):
                    regs = [rng.getrandbits(32) for _ in range(8)]
                    regs[4] = 0x8000
                    flags = 2 | (rng.getrandbits(12) & 0xCD5)
                    instruction = bytes((0xFE, 0xC0 | (sub << 3) | reg))
                    check(elf, instruction * 6 + b"", regs, flags,
                          bits, 6, 0xCD5,
                          f"{bits}-bit {name} byte reg={reg} seed={seed} "
                          f"iteration={iteration}")
                    count += 1
            print(f"PASS {bits}-bit {name} byte register", flush=True)

    # SETcc, all sixteen conditions, into every byte register. It reads the
    # flags the block was entered with and writes none, so the randomised
    # entry flags are what exercise it - and the result has to be exactly 0
    # or 1, which is what separates it from Jcc: the evaluator they share
    # returns a raw flag bit for the single-flag conditions.
    for bits in (32, 16):
        for cc in range(16):
            for reg in range(8):
                for iteration in range(max(rounds // 8, 1)):
                    regs = [rng.getrandbits(32) for _ in range(8)]
                    regs[4] = 0x8000
                    flags = 2 | (rng.getrandbits(12) & 0xCD5)
                    instruction = bytes((0x0F, 0x90 | cc, 0xC0 | reg))
                    code = instruction * 6 + bytes.fromhex("0f0b")
                    check(elf, code, regs, flags, bits, 6, 0xCD5,
                          f"{bits}-bit setcc cc={cc:x} reg={reg} seed={seed} "
                          f"iteration={iteration}")
                    count += 1
        print(f"PASS {bits}-bit setcc: 16 conditions x 8 byte registers",
              flush=True)

    # Both ModR/M directions, high/low-byte aliases, and read-modify-write RAM.
    edges = (0, 1, 0x7F, 0x80, 0xFF)
    for bits in (32, 16):
        for op in (0x00, 0x02, 0x08, 0x0A, 0x20, 0x22, 0x28, 0x2A, 0x30, 0x32, 0x38, 0x3A, 0x84):
            mask = 0xCD5 if op in (0, 2, 0x28, 0x2A, 0x38, 0x3A) else 0xCC5
            for source in range(8):
                regs = [rng.getrandbits(32) for _ in range(8)]
                regs[3], regs[4], regs[6] = 0x9000, 0x8000, 0x9000
                for destination in range(8):
                    code = bytes((op, 0xC0 | source << 3 | destination)) * 6 + b"\x0f\x0b"
                    check(elf, code, regs, 0xCD7, bits, 6, mask,
                          f"{bits}-bit byte opcode={op:02x} reg={source} rm={destination}")
                    count += 1
                for edge in edges:
                    memory = bytes([edge]) * 4096
                    # Absolute address: changing BL/BH must not move the test
                    # pointer on later repetitions in the 16-bit suite.
                    modrm = source << 3 | (5 if bits == 32 else 6)
                    instruction = bytes((op, modrm)) + (0x9000).to_bytes(bits // 8, "little")
                    code = instruction * 6 + b"\x0f\x0b"
                    check(elf, code, regs, 0xCD7, bits, 6, mask,
                          f"{bits}-bit byte opcode={op:02x} reg={source} RAM={edge:02x}", memory)
                    count += 1
            print(f"PASS {bits}-bit byte opcode={op:02x}: aliases and RAM", flush=True)
    # ADC and SBB, register form, both ModR/M directions and both widths.
    #
    # These are the family that reads CF, which is why they were interpreter
    # only until the compiler could materialise it.  The carry *in* is what
    # decides the outgoing CF - the lazy record has to name CC_ADC rather than
    # CC_ADD, and CC_SBB rather than CC_SUB - so every case is run with bit 0
    # of the entry flags both set and clear, and the edge operands below are
    # the ones where the two answers differ.
    edge32 = (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF)
    for bits in (32, 16):
        for op, name in ((0x11, "adc r/m,r"), (0x13, "adc r,r/m"),
                         (0x19, "sbb r/m,r"), (0x1b, "sbb r,r/m")):
            for cf in (0, 1):
                for source in range(8):
                    for destination in range(8):
                        regs = [rng.getrandbits(32) for _ in range(8)]
                        regs[4] = 0x8000
                        flags = 2 | cf | (rng.getrandbits(12) & 0xCD4)
                        code = bytes((op, 0xC0 | source << 3 | destination)) * 6                              + b""
                        check(elf, code, regs, flags, bits, 6, 0xCD5,
                              f"{bits}-bit {name} reg={source} rm={destination} "
                              f"cf={cf}")
                        count += 1
                for a in edge32:
                    for b in edge32:
                        regs = [0] * 8
                        regs[0], regs[1], regs[4] = a, b, 0x8000
                        flags = 2 | cf
                        # ADC EAX,ECX / SBB EAX,ECX and the other direction.
                        code = bytes((op, 0xC0 | 1 << 3 | 0)) * 6 + b""
                        check(elf, code, regs, flags, bits, 6, 0xCD5,
                              f"{bits}-bit {name} edge {a:#x},{b:#x} cf={cf}")
                        count += 1
            print(f"PASS {bits}-bit {name}: register pairs and carry edges",
                  flush=True)

    print(f"PASS: {count} differential cases; seed={seed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    parser.add_argument("--seed", type=int, default=386486)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--regression-only", action="store_true",
                        help="Run the fixed 16-bit SUB/AF reproducer only")
    args = parser.parse_args()
    if args.regression_only:
        check(args.elf, bytes.fromhex("29c8") * 6 + b"\x0f\x0b",
              [414477685, 3221989613, 3603976178, 4251314546, 32768,
               1446781, 3704745200, 2637385200], 0x896, 16, 6, 0xCD5,
              "16-bit SUB AF regression")
        print("PASS: 16-bit SUB AF regression")
    else:
        run(args.elf, args.seed, args.rounds)
