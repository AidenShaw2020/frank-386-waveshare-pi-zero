/*
 * Stand-in for <hardware/timer.h>. The core uses time_us_64() to implement the
 * guest's RDTSC; a monotonic host clock is the right equivalent.
 */
#ifndef HOSTBENCH_HW_TIMER_H
#define HOSTBENCH_HW_TIMER_H

#include <stdint.h>
#include <time.h>

static inline uint64_t time_us_64(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

#endif
