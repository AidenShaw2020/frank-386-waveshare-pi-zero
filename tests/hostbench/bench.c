/*
 * tiny386 core throughput benchmark.
 *
 * Runs the unmodified interpreter from src/i386.c against a set of endless
 * kernels and reports retired guest instructions per second. The same binary
 * is meant to be built for the host, for a Cortex-A in AArch32, and for
 * AArch64, so the numbers can be compared directly against the 1.8 MIPS the
 * RP2350 board reports for a real guest.
 *
 * No JIT, no devices, no BIOS. Every I/O and MMIO callback is a stub, so what
 * is measured is decode plus execute plus the memory path, and nothing else.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#include "i386.h"
#include "payload.inc"

#define MEM_SIZE   (8u << 20)      /* 8 MB, matching the board's PSRAM */
#define CODE_BASE  0x00010000u
#define STACK_TOP  0x000F0000u

/* GPR indices as the core numbers them. */
enum { R_EAX, R_ECX, R_EDX, R_EBX, R_ESP, R_EBP, R_ESI, R_EDI };

static uint64_t now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* ---- device stubs: the benchmark never touches any of these ---- */
static int   cb_pic_read_irq(void *o)                       { (void)o; return -1; }
static u8    cb_io_read8  (void *o, int p)                  { (void)o; (void)p; return 0xFF; }
static void  cb_io_write8 (void *o, int p, u8 v)            { (void)o; (void)p; (void)v; }
static u16   cb_io_read16 (void *o, int p)                  { (void)o; (void)p; return 0xFFFF; }
static void  cb_io_write16(void *o, int p, u16 v)           { (void)o; (void)p; (void)v; }
static u32   cb_io_read32 (void *o, int p)                  { (void)o; (void)p; return 0xFFFFFFFFu; }
static void  cb_io_write32(void *o, int p, u32 v)           { (void)o; (void)p; (void)v; }
static int   cb_io_read_string (void *o, int p, uint8_t *b, int n, int w)
                                                            { (void)o; (void)p; (void)b; (void)n; (void)w; return 0; }
static int   cb_io_write_string(void *o, int p, uint8_t *b, int n, int w)
                                                            { (void)o; (void)p; (void)b; (void)n; (void)w; return 0; }
static u8    cb_mem_read8 (void *o, uword a)                { (void)o; (void)a; return 0xFF; }
static void  cb_mem_write8(void *o, uword a, u8 v)          { (void)o; (void)a; (void)v; }
static u16   cb_mem_read16(void *o, uword a)                { (void)o; (void)a; return 0xFFFF; }
static void  cb_mem_write16(void *o, uword a, u16 v)        { (void)o; (void)a; (void)v; }
static u32   cb_mem_read32(void *o, uword a)                { (void)o; (void)a; return 0xFFFFFFFFu; }
static void  cb_mem_write32(void *o, uword a, u32 v)        { (void)o; (void)a; (void)v; }
static bool  cb_mem_write_string(void *o, uword a, uint8_t *b, int n)
                                                            { (void)o; (void)a; (void)b; (void)n; return true; }

static void wire_stubs(CPU_CB *cb)
{
	cb->pic = NULL;          cb->pic_read_irq = cb_pic_read_irq;
	cb->io = NULL;
	cb->io_read8 = cb_io_read8;     cb->io_write8 = cb_io_write8;
	cb->io_read16 = cb_io_read16;   cb->io_write16 = cb_io_write16;
	cb->io_read32 = cb_io_read32;   cb->io_write32 = cb_io_write32;
	cb->io_read_string = cb_io_read_string;
	cb->io_write_string = cb_io_write_string;
	cb->iomem = NULL;
	cb->iomem_read8 = cb_mem_read8;     cb->iomem_write8 = cb_mem_write8;
	cb->iomem_read16 = cb_mem_read16;   cb->iomem_write16 = cb_mem_write16;
	cb->iomem_read32 = cb_mem_read32;   cb->iomem_write32 = cb_mem_write32;
	cb->iomem_write_string = cb_mem_write_string;
}

/*
 * Run one kernel for about `seconds` and return millions of guest instructions
 * per second. Returns a negative value if the guest stopped making progress,
 * which would mean the payload is wrong rather than slow.
 */
static double run_kernel(const kernel_t *k, char *mem, double seconds, int cpu_gen)
{
	CPU_CB *cb = NULL;
	CPUI386 *cpu = cpui386_new(cpu_gen, mem, MEM_SIZE, &cb);
	if (!cpu || !cb) {
		fprintf(stderr, "cpui386_new failed\n");
		return -1.0;
	}
	wire_stubs(cb);

	memset(mem, 0, MEM_SIZE);
	memcpy(mem + CODE_BASE, k->code, k->len);

	cpui386_reset_pm(cpu, CODE_BASE);
	cpui386_set_gpr(cpu, R_EBX, BENCH_MASK);   /* wrap mask for the walk */
	cpui386_set_gpr(cpu, R_ESI, 0);
	cpui386_set_gpr(cpu, R_ESP, STACK_TOP);
	cpui386_set_gpr(cpu, R_EBP, STACK_TOP);

	const int chunk = 100000;

	/* Warm up: let any lazy state settle before the clock starts. */
	for (int i = 0; i < 20; i++)
		cpui386_step(cpu, chunk);

	uint32_t cs, ip; int halt = 0;
	cpui386_get_state(cpu, &cs, &ip, &halt);
	if (halt) {
		fprintf(stderr, "%s: guest halted during warmup (ip=%08x)\n", k->name, ip);
		cpui386_delete(cpu);
		return -1.0;
	}

	uint64_t retired = 0;
	unsigned long prev = (unsigned long)cpui386_get_cycle(cpu);
	uint64_t t0 = now_ns(), t1;
	do {
		cpui386_step(cpu, chunk);
		unsigned long cur = (unsigned long)cpui386_get_cycle(cpu);
		retired += (uint64_t)(cur - prev);   /* wraps correctly on 32-bit long */
		prev = cur;
		t1 = now_ns();
	} while ((double)(t1 - t0) / 1e9 < seconds);

	cpui386_get_state(cpu, &cs, &ip, &halt);
	cpui386_delete(cpu);

	if (halt || retired == 0) {
		fprintf(stderr, "%s: guest made no progress\n", k->name);
		return -1.0;
	}
	return (double)retired / ((double)(t1 - t0) / 1e9) / 1e6;
}

int main(int argc, char **argv)
{
	double seconds = (argc > 1) ? atof(argv[1]) : 2.0;
	int cpu_gen = (argc > 2) ? atoi(argv[2]) : 3;

	char *mem = malloc(MEM_SIZE);
	if (!mem) {
		fprintf(stderr, "cannot allocate %u bytes of guest RAM\n", MEM_SIZE);
		return 1;
	}

	printf("tiny386 interpreter throughput\n");
	printf("  guest RAM   : %u MB at %p\n", MEM_SIZE >> 20, (void *)mem);
	printf("  cpu_gen     : %d\n", cpu_gen);
	printf("  per kernel  : %.1f s\n", seconds);
	printf("  pointer size: %zu bytes, long: %zu bytes\n",
	       sizeof(void *), sizeof(long));
	printf("\n%-8s %10s   %s\n", "kernel", "MIPS", "what it measures");
	printf("%-8s %10s   %s\n", "------", "----", "----------------");

	double alu = 0.0;
	for (unsigned i = 0; i < NKERNELS; i++) {
		double mips = run_kernel(&kernels[i], mem, seconds, cpu_gen);
		if (mips < 0) {
			printf("%-8s %10s   %s\n", kernels[i].name, "FAILED", kernels[i].desc);
			continue;
		}
		if (i == 0)
			alu = mips;
		printf("%-8s %10.2f   %s\n", kernels[i].name, mips, kernels[i].desc);
	}

	if (alu > 0.0)
		printf("\nThe alu-to-stride ratio is the memory penalty: a cached machine\n"
		       "stays within a small factor, an uncached one does not.\n");

	free(mem);
	return 0;
}
