#ifndef NJIT_VGA_ARENA_H
#define NJIT_VGA_ARENA_H

#include <stdint.h>

/*
 * Lending the top of gfx_buffer to the native JIT.
 *
 * gfx_buffer is 256 KB because the planar (mode X / EGA) write path stores
 * four host bytes per guest byte: `((uint32_t *)vga_ram)[addr]` with addr up
 * to 0xFFFF.  No other path needs anything like that much.  Reading
 * vga_mem_write():
 *
 *   - chain 4 (mode 13h) writes `vga_ram[addr]` with addr masked to 0x1FFFF,
 *     so it cannot pass byte 131071.  It is the one path with no bounds check
 *     against vga_ram_size, which is why the borrowed region starts well
 *     above that limit rather than at it.
 *   - odd/even (text) computes ((addr & ~1) << 1) | plane from an addr below
 *     0x10000, so it is bounded by the same 131072 and is checked as well.
 *   - the planar path and the PCI linear framebuffer aperture in pc.c can
 *     reach the whole 256 KB.  Those are the only writers hooked below.
 *
 * So for anything running in mode 13h or in text - which is Doom, Draci
 * historie and most DOS games - the top of the buffer is dead SRAM, and the
 * JIT can have it.  A guest that does reach into it takes the region back
 * (njit_vga_arena_release), which flushes the JIT and drops it to the small
 * permanent arena in .data.  Planar games therefore keep working exactly as
 * before; they simply run without the large arena.
 *
 * Safety rests on two properties of the existing code, both checked:
 *
 *   1. A JIT block writes to VGA memory only through the chain-4 window
 *      the njit_vga_write table below, whose `limit` is computed so that the
 *      store stays under NJ_VGA_ARENA_OFF, and never through the PCI
 *      aperture or a masked planar operation.  Every write that could touch
 *      the top of gfx_buffer therefore still comes from the interpreter.  (Before NJIT_VGA_DIRECT no block wrote to VGA memory at
 *      all: nj_v6_emit_mem_guard side exited on the whole aperture.)
 *   2. The JIT is entered from inside cpu_exec1 and blocks return before it
 *      resumes, so when a store reaches vga_mem_write no block is executing
 *      and no arena address is on the stack.
 *
 * Together those make the release synchronous and safe at the point of the
 * offending write.
 */

/* Offset and length inside gfx_buffer.  128 KB would already be correct; the
 * extra 64 KB of margin is deliberate insurance against a writer this audit
 * missed, and costs nothing because the region is unused either way. */
#define NJ_VGA_ARENA_OFF   (192u * 1024u)
#define NJ_VGA_ARENA_LEN   (64u * 1024u)

#ifdef __cplusplus
extern "C" {
#endif

/* Called once the framebuffer exists and has been cleared.  `bytes` may be 0
 * to say there is nothing to lend (a build with a smaller gfx_buffer). */
void njit_vga_arena_offer(void *base, unsigned bytes);

/* The guest is about to touch the region.  Flushes the JIT and hands it back.
 * Cheap and idempotent once the region is already released. */
void njit_vga_arena_release(void);

/* The VGA mode changed, so a mode that could reach the region may have been
 * left behind.  Only sets a flag; the arena is taken again lazily on the
 * compile path, where no block can be executing. */
void njit_vga_arena_rearm(void);

/*
 * Direct native writes to the 0xA0000-0xBFFFF aperture.
 *
 * Measured on the DRACIHIS opening: the shared memory guard refused 1 500 083
 * accesses in twenty seconds, every one of them abandoning the rest of a
 * compiled block, and the aperture was the ONLY thing it ever refused - the
 * TLB stopped sixteen accesses and the code-page bitmap 135.  Splitting the
 * refusals by direction showed all 1 500 083 were WRITES and not one was a
 * read, which is what makes this table write-only: a read in a latched mode
 * loads s->latch, and a native load cannot do that.
 *
 * Two configurations reduce vga_mem_write() to storing one byte:
 *
 *   - chain 4 with graphics memory map 0 (mode 13h): `vga_ram[addr]`, any
 *     operand size, all four planes enabled.
 *   - the standard latched path when it does nothing at all to the value -
 *     write mode 0, no rotate, no logical function, no set/reset, bit mask
 *     0xff - and exactly one plane is write-enabled.  Then the dword store
 *     `((uint32_t *)vga_ram)[addr]` changes exactly one byte,
 *     `vga_ram[addr * 4 + plane]`.  This is mode X, which is what DRACIHIS
 *     runs in, and it is byte-only: consecutive guest addresses are four
 *     bytes apart, so a 16- or 32-bit store is not a store at all.
 *
 * Both are the same shape to generated code: `ram[offset << shift]`, where
 * offset is the guest physical address minus 0xA0000.  One table entry per
 * operand size, so the size-2 and size-4 entries are simply left empty in
 * mode X.  `ram` is NULL when the fast path does not apply, which is how a
 * planar or masked configuration - and any mode this audit did not admit -
 * goes back to the interpreter.
 *
 * `limit` is the largest offset the entry may be used for.  It keeps the
 * store inside vga_ram, inside the 64 KB or 128 KB window the mapping mode
 * allows, and - this is the important one - below NJ_VGA_ARENA_OFF, so a
 * native write can never reach the region lent to the JIT.  That is what
 * keeps the arena's safety argument intact now that blocks write here at all.
 *
 * Maintained by njit_vga_write_update() in vga.c, which is called from every
 * site in that file that changes what the decision rests on: the sequencer
 * and graphics register writes in vga_ioport_write(), the VBE bank register,
 * vbe_update_vgaregs() and vga_initmode().
 *
 * The generated guard reads the fields by their offsets, so their order is
 * fixed; i386.c static-asserts it.
 */
struct njit_vga_write {
    uint8_t *ram;          /* host address for aperture offset 0, or NULL */
    uint32_t shift;        /* 0 for chain 4, 2 for one plane in mode X */
    uint32_t limit;        /* largest usable aperture offset, inclusive */
};
extern volatile struct njit_vga_write njit_vga_write[3];  /* size 1, 2, 4 */

#ifdef __cplusplus
}
#endif

#endif /* NJIT_VGA_ARENA_H */
