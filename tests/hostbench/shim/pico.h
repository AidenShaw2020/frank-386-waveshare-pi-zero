/*
 * Minimal stand-in for <pico.h> so the tiny386 core builds off the RP2350.
 *
 * The core only uses the SDK for placement attributes. Off the target there is
 * no XIP flash to move code out of, so they all collapse to nothing.
 */
#ifndef HOSTBENCH_PICO_H
#define HOSTBENCH_PICO_H

#include <stdint.h>
#include <stddef.h>

#define __not_in_flash(...)
#define __not_in_flash_func(fn) fn
#define __in_flash(...)
#define __scratch_x(g)
#define __scratch_y(g)
#define __time_critical_func(fn) fn
#define __always_inline inline __attribute__((always_inline))

#endif
