"""Native intra-block links: a branch into the block's own body, not through C.

A compiled trace whose taken branch lands on an instruction boundary it
already holds normally leaves through the epilogue, spills eight guest
registers, is looked up again in C and reloads them.  With a link trampoline
it branches straight to that boundary instead.  Measured on DRACIHIS, that is
1,228,482 chain breaks in twenty seconds.  These tests check the three things that
can go wrong with that: the loop must retire the same architectural state as
the interpreter and an independent x86 engine, it must never retire more than
the dispatcher's step budget, and it must refuse to link when re-entry would
have needed flags the C path materialises.

The generated code is the real ELF's, executed under Unicorn's Cortex-M33.
"""
import argparse

from elf_harness import Firmware
from run_differential import x86_reference

# 16-bit real-mode loop, six instructions, ending in a backward JNZ to its own
# first instruction.  Nothing here is INC/DEC, so nothing forces CF to be
# materialised on entry and the trace qualifies for a link.
#   0: 89 d1     mov cx,dx
#   2: 01 ce     add si,cx
#   4: 89 f7     mov di,si
#   6: 31 ed     xor bp,bp
#   8: 29 d8     sub ax,bx
#   a: 75 f4     jnz 0
LOOP = bytes.fromhex("89d101ce89f731ed29d875f4")
LOOP_INSNS = 6

# A loop whose head is the second instruction, so the back edge targets a
# boundary inside the block rather than its entry.
#   0: 89 d1     mov cx,dx      <- runs once
#   2: 89 f7     mov di,si      <- loop head
#   4: 01 ce     add si,cx
#   6: 89 f3     mov bx,si
#   8: 31 ed     xor bp,bp
#   a: 29 c8     sub ax,cx
#   c: 75 f4     jnz 2
INNER = bytes.fromhex("89d189f701ce89f331ed29c875f4")
INNER_INSNS = 7

# The same shape, but it opens with DEC BP.  The compiler records
# needs_refresh_cf for that, nj_exec_loop() calls refresh_flags() before
# entry, and a native back edge would skip it - so the link must be refused.
#   0: 4d        dec bp
#   1: 89 d1     mov cx,dx
#   3: 89 f7     mov di,si
#   5: 89 cb     mov bx,cx
#   7: 31 ff     xor di,di
#   9: 29 d8     sub ax,bx
#   b: 75 f3     jnz 0
CF_LOOP = bytes.fromhex("4d89d189f789cb31ff29d875f3")
CF_LOOP_INSNS = 7

FLAG_MASK = 0xCD5  # architecturally defined arithmetic flags


def run_once(elf, code, registers, limit):
    """Run the compiled block once with the given step budget."""
    jit = Firmware(elf)
    try:
        jit.prepare(code, registers, bits=16, flags=2)
        block = jit.compile_block()
        if not block:
            raise AssertionError("trace unexpectedly rejected")
        info = {
            "insns": jit.block_field(block, "insns", 1),
            "njl_link": jit.block_field(block, "njl_link", 1),
        }
        jit.put("njl_acc", 0)
        jit.put("njl_pos", 0)
        jit.put("njl_limit", limit)
        info["done"] = jit.call(jit.block_field(block, "code"), jit.CPU, 1)
        info["acc"] = jit.get("njl_acc")
        info["state"] = jit.snapshot()
    finally:
        jit.close()
    return info


def compare(elf, code, registers, done, label):
    """The same instruction count through the interpreter and through x86."""
    reference = Firmware(elf)
    try:
        reference.prepare(code, registers, bits=16, flags=2)
        if reference.call("cpu_exec1", reference.CPU, done) != 1:
            raise AssertionError(f"{label}: interpreter fault")
        expected = reference.snapshot()
    finally:
        reference.close()
    independent = x86_reference(code, registers, 2, 16, done)
    expected["flags"] &= FLAG_MASK
    independent["flags"] &= FLAG_MASK
    if expected != independent:
        raise AssertionError(f"{label}: interpreter and x86 disagree\n"
                             f"{expected}\n{independent}")
    return expected


def check(elf, code, registers, limit, expect_done, label, expect_link=True):
    info = run_once(elf, code, registers, limit)
    if bool(info["njl_link"]) != expect_link:
        raise AssertionError(f"{label}: njl_link={info['njl_link']}, "
                             f"expected {int(expect_link)}")
    if info["done"] != expect_done:
        raise AssertionError(f"{label}: retired {info['done']}, "
                             f"expected {expect_done}")
    if info["done"] > limit and limit:
        raise AssertionError(f"{label}: retired {info['done']} past a "
                             f"budget of {limit}")
    expected = compare(elf, code, registers, info["done"], label)
    actual = dict(info["state"])
    actual["flags"] &= FLAG_MASK
    if actual != expected:
        differences = {key: (actual[key], expected[key])
                       for key in actual if actual[key] != expected[key]}
        raise AssertionError(f"{label}: JIT and interpreter differ after "
                             f"{info['done']} instructions: {differences}")
    print(f"PASS {label}: {info['done']} instructions, njl_link="
          f"{info['njl_link']}")
    return info


def run(elf):
    # AX=5, BX=1: the back edge is taken four times and the fifth pass falls
    # through, so a linked block retires all five passes in one native entry.
    registers = (5, 0, 1, 1, 0x1000, 0x20, 0x30, 0x40)
    check(elf, LOOP, registers, 512, 5 * LOOP_INSNS, "loop runs to completion")

    # The trampoline never starts a pass it cannot finish, so a budget that
    # allows exactly two passes must retire exactly two and stop on the taken
    # back edge.  This is what keeps nj_exec_loop()'s accounting honest.
    check(elf, LOOP, registers, 2 * LOOP_INSNS, 2 * LOOP_INSNS,
          "budget stops the loop")
    for budget in (LOOP_INSNS, 2 * LOOP_INSNS - 1, 3 * LOOP_INSNS + 1):
        info = run_once(elf, LOOP, registers, budget)
        if info["done"] > budget:
            raise AssertionError(f"budget {budget}: retired {info['done']}")
    print("PASS: no budget is ever exceeded")

    # A zeroed limit is what generated code sees when it is called outside the
    # dispatcher, including from the other suites in this directory.  It must
    # behave exactly as an unlinked block: one pass, then exit.
    check(elf, LOOP, registers, 0, LOOP_INSNS, "zero budget takes no link")

    # The loop head does not have to be the block's first instruction: here
    # the back edge lands on boundary 1, which the earlier self-link-only
    # version could not use at all.
    inner = (5, 0, 1, 0, 0x1000, 0x20, 0x30, 0x40)
    check(elf, INNER, inner, 512, 1 + 5 * (INNER_INSNS - 1),
          "back edge to an inner boundary")
    check(elf, INNER, inner, 0, INNER_INSNS, "inner: zero budget takes no link")
    for budget in (INNER_INSNS, 2 * INNER_INSNS, 3 * INNER_INSNS + 2):
        info = run_once(elf, INNER, inner, budget)
        if info["done"] > budget:
            raise AssertionError(f"inner budget {budget}: retired {info['done']}")
    print("PASS: no inner-link budget is exceeded")

    # An entry that needs CF materialised from the lazy record used to be
    # refused a link outright, because a native back edge skips the
    # refresh_flags() call nj_exec_loop() makes at block entry.  The
    # trampoline now makes that call itself, so the block links like any
    # other - and the check below is what says it is allowed to: `check`
    # compares the JIT's final registers *and flags* against the
    # interpreter's after the same number of instructions, so a link that got
    # CF wrong would fail here rather than pass quietly.
    check(elf, CF_LOOP, (4, 0, 1, 0, 0x1000, 0x20, 0x30, 0x40), 512,
          4 * CF_LOOP_INSNS, "DEC entry links and keeps CF")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    run(parser.parse_args().elf)
    print("PASS: native intra-block links")


if __name__ == "__main__":
    main()
