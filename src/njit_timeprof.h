/**
 * frank-386 - cycle-exact top-level split of core 0.
 *
 * Why this exists when there are already two profilers.
 *
 * PC_SAMPLE buckets the RAM text and ranks the buckets, which is fine for
 * finding a hot function and useless for apportioning core 0: at the bucket
 * sizes that fit in SRAM one bucket holds several functions, and a bucket is
 * labelled with whatever symbols happen to *start* in it.  That is how the
 * OPL mixer got read as 10-37% of core 0 in this tree when the firmware's own
 * timing says the periodic half of it is 1.3%.
 *
 * FRANK_PROF in audiodiag.h does measure cycles, but it only exists when
 * FRANK_AUDIO_DIAG is on, and that brings a volatile read on every guest
 * store - about 5% - so it changes the thing being measured.
 *
 * This is the smallest instrument that answers "where does core 0 go":
 * six 64-bit accumulators in SRAM (48 bytes, which is what there is room
 * for) and two DWT cycle reads per bracket.  Off by default; build with
 * -DNJIT_TIMEPROF=ON.
 *
 * The brackets nest deliberately - JIT is inside CPU, OPL writes are inside
 * CPU as well because a guest OUT triggers them - so read them as shares of
 * TOTAL, not as a partition.
 */
#ifndef NJIT_TIMEPROF_H
#define NJIT_TIMEPROF_H

#include <stdint.h>

#ifndef NJIT_TIMEPROF
#define NJIT_TIMEPROF 0
#endif

enum {
    NJT_TOTAL = 0,   /* one whole pc_step()                                */
    NJT_CPU,         /* cpui386_step(): interpreter and JIT together       */
    NJT_JIT,         /* nj_try_execute(): inside NJT_CPU                   */
    NJT_ADLIB,       /* adlib_core0(): the periodic OPL refill             */
    NJT_OPLW,        /* rendering forced by a guest OPL register write     */
    NJT_CALLS,       /* pc_step() calls, so the others can be per-call     */
    /* Core 1's own accumulators.  Its DWT cycle counter is a separate
     * peripheral from core 0's, so njt_arm() has to run there too; the
     * question they answer is whether core 1 has the headroom to take
     * work off core 0. */
    NJT_C1_BUSY,     /* repeat_me_often(): video, config, everything       */
    NJT_C1_LOOPS,    /* core 1 loop iterations                             */
    /* nj_try_execute() runs at every taken backward branch, not only where a
     * block exists, so NJT_JIT divided by the hit count is not the cost of an
     * entry - it is the cost of an entry plus every probe that found nothing.
     * These two separate the halves. */
    NJT_PROBES,      /* nj_try_execute() calls                             */
    NJT_NATIVE,      /* nj_exec_chain(): inside NJT_JIT, hits only         */
    NJT_COUNT
};

#if NJIT_TIMEPROF

#ifdef __cplusplus
extern "C" {
#endif
extern volatile uint64_t g_njt[NJT_COUNT];
#ifdef __cplusplus
}
#endif

#define NJT_DEMCR      (*(volatile uint32_t *)0xE000EDFCu)
#define NJT_DWT_CTRL   (*(volatile uint32_t *)0xE0001000u)
#define NJT_DWT_CYCCNT (*(volatile uint32_t *)0xE0001004u)

/* Idempotent; the counter is free-running once enabled. */
static inline void njt_arm(void)
{
    NJT_DEMCR |= (1u << 24);      /* TRCENA */
    NJT_DWT_CTRL |= (1u << 0);    /* CYCCNTENA */
}

/* Differences are taken in uint32 so one wrap inside an interval is still
 * right; the accumulators are 64-bit so the totals are too. */
#define NJT_T(v)        uint32_t v = NJT_DWT_CYCCNT
#define NJT_ADD(v, i)   g_njt[i] += (uint32_t)(NJT_DWT_CYCCNT - (v))
#define NJT_COUNT_ONE(i) g_njt[i]++

#else

static inline void njt_arm(void) { }
#define NJT_T(v)         do { } while (0)
#define NJT_ADD(v, i)    do { } while (0)
#define NJT_COUNT_ONE(i) do { } while (0)

#endif /* NJIT_TIMEPROF */

#endif /* NJIT_TIMEPROF_H */
