# Host benchmark for the tiny386 core

Builds `src/i386.c` off the RP2350 and measures how many guest instructions per
second the **interpreter** retires. No JIT, no devices, no BIOS: every I/O and
MMIO callback is a stub, so what is measured is decode, execute and the memory
path, and nothing else.

The point is to answer one question with a number rather than an argument: how
much of the board's throughput is lost to the memory system rather than to the
CPU. On the RP2350 guest RAM is QSPI PSRAM with no data cache; on anything with
a normal cache hierarchy it is an ordinary array.

## Kernels

Each kernel is an endless loop of 32-bit protected-mode code, generated and
verified against capstone by `gen_payload.py`.

| kernel   | what it does                              | what it isolates          |
|----------|-------------------------------------------|---------------------------|
| `alu`    | register-only ALU, no data memory         | pure interpreter dispatch |
| `seq`    | one 32-bit load per 64 B, walking forward | streaming reads           |
| `stride` | one 32-bit load per 4160 B                | cache- and TLB-hostile    |
| `store`  | one 32-bit store per 4160 B               | the store path            |
| `mixed`  | push/pop plus a load                      | shape closer to real code |

**The `alu`-to-`stride` ratio is the result.** A machine whose guest RAM sits
behind a cache stays within a small factor. One without a cache does not.

## Building

```sh
make                    # native
make ARCH=arm32         # Cortex-A7, AArch32 - the mode that reuses the Thumb-2 backend
make ARCH=arm64         # Cortex-A53, AArch64
SRC=../../src make      # if the firmware tree is not at /work/src
```

`SRC` defaults to `/work/src`, which is where the container mount puts it:

```sh
docker run --rm -v "$PWD/../..:/work:ro" -v "$PWD:/bench" -w /bench gcc:13 make
docker run --rm -v "$PWD:/bench" -w /bench gcc:13 ./bench-native 2 3
```

Arguments are seconds per kernel and CPU generation (3 = 386, 4 = 486).

## Measured so far

| target                        | alu    | stride | ratio |
|-------------------------------|--------|--------|-------|
| x86-64 host (Docker, gcc 13)  | 120.2  | 96.6   | 1.24  |
| RP2350 @ 504 MHz              | -      | -      | -     |

For reference, the board reports about 1.8 MIPS on a real DOS guest, which is a
different and heavier workload than these kernels - it includes VGA, audio and
interrupt work. The board row above is deliberately left empty: it needs a
firmware build that runs these same kernels through the same interpreter, so
that the comparison is like for like.

## Note on 32-bit targets

`cpu->cycle` is a `long`, so it is 32 bits on AArch32 and wraps after about two
billion instructions. The harness accumulates per-chunk differences in unsigned
arithmetic, which is correct across the wrap.
