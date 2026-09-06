/* Assemble with arm-none-eabi-as -mcpu=cortex-m33 -mthumb, then objdump -d.
 * Independent assembler reference for hand-encoded helpers in i386.c. */
.syntax unified
.thumb
.text
.global jit_encoding_reference
.thumb_func
jit_encoding_reference:
    pop {lr}
    bfi r4, r3, #0, #8
    bfi r4, r3, #8, #8
    bfi r4, r3, #0, #16
    bx lr
