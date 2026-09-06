# Firmware JIT differential tests

These tests run the **actual functions and generated Thumb code from an ELF**
under Unicorn's Cortex-M33 model. They compare the result with the same ELF's
interpreter and, for supported test scenarios, Unicorn's independent x86
engine. There is no replacement Python implementation of the JIT emitter.

## Run

Use Python 3 and an unstripped RP2350 firmware ELF built with `NATIVE_JIT=ON`.
Install dependencies in a virtual environment:

```text
python -m venv .venv-jit
.venv-jit/Scripts/python -m pip install -r tests/jit/requirements.txt
.venv-jit/Scripts/python tests/jit/run_differential.py build/z0p2-386-504MHz-P166-I2S-v1.05.elf --rounds 16
.venv-jit/Scripts/python tests/jit/run_guards.py build/z0p2-386-504MHz-P166-I2S-v1.05.elf
.venv-jit/Scripts/python tests/jit/run_mixed.py build/z0p2-386-504MHz-P166-I2S-v1.05.elf
.venv-jit/Scripts/python tests/jit/run_arena.py build/z0p2-386-504MHz-P166-I2S-v1.05.elf
.venv-jit/Scripts/python tests/jit/run_link.py build/z0p2-386-504MHz-P166-I2S-v1.05.elf
.venv-jit/Scripts/python tests/jit/run_vga.py build/z0p2-386-504MHz-P166-I2S-v1.05.elf
.venv-jit/Scripts/python tests/jit/run_seg.py build/z0p2-386-504MHz-P166-I2S-v1.05.elf
```

Two more tools report on the compiler rather than testing it. They need
`capstone` in the same virtual environment:

```text
.venv-jit/Scripts/python tests/jit/coverage_matrix.py <elf>
.venv-jit/Scripts/python tests/jit/code_shape.py <elf> [--dump "reg ALU x6"]
```

On Unix, use `.venv-jit/bin/python`. The firmware itself still needs the Pico
SDK and ARM cross compiler. The tests do not use SWD or change the board.

`--regression-only` runs a fixed six-SUB sequence. The preserved `opl-block8`
ELF fails with flags `(JIT=0x05, interpreter=0x15, independent x86=0x15)`;
the updated partial-register writers must pass.

## The files

- `run_differential.py` - straight-line register, byte and RAM suites.
- `run_guards.py` - a failed memory guard must exit before the instruction.
- `run_mixed.py` - the cases the two above cannot reach because they repeat a
  single instruction: interleaved flag families, stack sequences, shifts,
  NOT/NEG, partial traces, and a guard side exit that follows a flag-setting
  instruction. This suite is what makes the deferred lazy-flag flush
  trustworthy; both of its rules have a negative control that fails when the
  rule is removed.
- `run_arena.py` - the JIT keeps working when its code arena moves into the
  top of `gfx_buffer` (see `src/njit_vga_arena.h`). Offers a region to the real
  compiler, compiles until the permanent arena fills, and checks that a block
  built **inside** the lent region retires correctly, and that releasing the
  region puts the arena back without breaking anything.
- `run_link.py` - native intra-block links. A taken branch whose target is an
  instruction boundary the block already holds jumps straight there instead of
  spilling the guest registers and returning to the C dispatcher. Checks that
  a linked loop retires the same architectural state as the interpreter and as
  Unicorn's x86 after every pass, that the dispatcher's step budget is never
  exceeded - including a zero budget, which is what the suites above produce
  when they call generated code directly - and that a block needing CF
  materialised on entry refuses to link at all.
- `run_vga.py` - direct native writes to the 0xA0000 aperture. When the
  aperture reduces to a plain byte store - chain 4, or a single-plane mode X
  write that does nothing to the value - the memory guard returns a host
  pointer instead of side-exiting. Drives that decision through
  `njit_vga_write`: chain-4 byte and dword stores compared against the
  interpreter, an unpublished table, a read (never direct, because a latched
  mode would have to load `s->latch`), an offset past the entry's limit, an
  address straddling the start of the aperture, and mode X stores landing at
  `ram[offset << 2]` and nowhere else.
- `run_seg.py` - segment loads inside a trace. `LES`, `LDS`, `MOV Sreg,r/m16`
  and `MOV r/m16,Sreg` compile in real mode and VM86, where a segment load is
  four stores. The delicate part is that a block bakes `cpu->seg[x].base` into
  every memory operand, so each case also addresses memory *through* the
  segment it just loaded and compares the whole segment table, a window of
  guest memory and the architectural state against the interpreter - which is
  `set_seg()` itself. Includes a case where one segment is dynamic and another
  is still baked, and the regression that shipped once: a backward branch to a
  boundary *before* the segment write must not be linked, or those operands
  re-run against the base the write invalidated. That last case was verified
  against the bug - remove the `seg_write_at` condition in
  `nj_compile_v6_trace()` and it fails.
- `coverage_matrix.py` - which of 77 common x86 forms the compiler admits, in
  16- and 32-bit mode, with the emitted bytes per guest instruction. Reports
  admission, not correctness.
- `code_shape.py` - disassembles what the compiler emits for real guest
  sequences. Used to find that a guest `add eax,ecx` cost ten Thumb
  instructions, nine of them a lazy-flag record the next instruction
  overwrote.

## What is exercised

- 16-bit real-mode and flat 32-bit protected-mode trace bodies, paging off.
- Randomized GPR upper bits and arithmetic flags, with a reproducible seed.
- MOV, basic register arithmetic, byte immediate arithmetic, MOVZX/MOVSX.
- CBW/CWDE and CWD/CDQ, including preservation of preceding lazy flags.
- Byte ADD/OR/AND/SUB/XOR/CMP/TEST: both ModR/M directions, all 64 register
  aliases for each opcode, all eight byte registers with absolute RAM operands,
  memory values 00/01/7F/80/FF, and comparison of the whole 4 KB data page.
- MMIO/bounds/code-write guard exits after one successfully retired instruction.
- Interleaved arithmetic/logical/flagless instructions, in both orders, at two
  starting flag states, including sequences the compiler only partly accepts.
- PUSH/POP/PUSH-imm sequences with SP inside the compared page, both stack
  widths, including `push sp` (the 386 pushes the value before the decrement).
- SHL/SHR/SAR by 1, 2, 4, 7 and 15 over every register, in native 16-bit, in
  32-bit, and 16-bit through an operand-size prefix. OF is compared only for a
  count of one, where it is architecturally defined.
- NOT and NEG in all three widths, over operand values including zero, which is
  the case that decides NEG's carry.

Only defined arithmetic flags are compared for logical operations (AF is
undefined). Register values, next IP and the data page are compared exactly.
The compiler must accept the entire expected sequence; a rejection or partial
trace is a failure, not a skipped test. The interpreter tests are straight-line
and contain no backward edges, so they cannot accidentally execute the JIT.

ELF symbols and structure offsets are discovered from `.symtab` and DWARF.
Memory/stack addresses are deliberately synthetic; the TLB capacity is the
RP2350 target's 512 entries. A fresh machine is used for each test. SDK
`memcpy`, `memmove` and `memset` are replaced with their ordinary C semantics
because board startup normally initializes their ROM service pointers.

## Limits

This is the first test layer, not full 386 validation. It does not yet exercise
the narrow full-loop compilers, paging/VM86, arbitrary descriptors, interrupts,
fault delivery, I/O, x87, native chaining or hardware cache coherence. Guard
tests verify side exits, not subsequent interpreter exception delivery.
Unicorn's x86 model is an additional reference, not physical 386 hardware.
No speed conclusions can be drawn from host execution time.

`encoding_reference.S` can be assembled with `arm-none-eabi-as -mcpu=cortex-m33
-mthumb` and disassembled with `arm-none-eabi-objdump -d` to independently check
the BFI and single-register POP encodings against the production emitter.
