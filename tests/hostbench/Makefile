# Build the tiny386 interpreter as a host benchmark.
#
#   make                 native build
#   make ARCH=arm32      Cortex-A7, AArch32 - the mode that reuses the Thumb-2 backend
#   make ARCH=arm64      AArch64
#
# SRC points at the firmware tree; override it if this lives elsewhere.

SRC ?= /work/src
SHIM := shim

CC ?= gcc
OPT ?= -O2
CFLAGS := $(OPT) -fno-strict-aliasing -std=gnu11 -w \
          -I$(SHIM) -I$(SRC) -I. -DNDEBUG
LDFLAGS := -lm

ifeq ($(ARCH),arm32)
CC := arm-linux-gnueabihf-gcc
CFLAGS += -marm -mcpu=cortex-a7 -mfpu=neon-vfpv4
TAG := arm32
else ifeq ($(ARCH),arm64)
CC := aarch64-linux-gnu-gcc
CFLAGS += -mcpu=cortex-a53
TAG := arm64
else
TAG := native
endif

BIN := bench-$(TAG)
OBJS := bench-$(TAG).o i386-$(TAG).o fpu-$(TAG).o

all: $(BIN)

payload.inc:
	python3 gen_payload.py

bench-$(TAG).o: bench.c payload.inc
	$(CC) $(CFLAGS) -c $< -o $@

i386-$(TAG).o: $(SRC)/i386.c
	$(CC) $(CFLAGS) -c $< -o $@

fpu-$(TAG).o: $(SRC)/fpu.c
	$(CC) $(CFLAGS) -c $< -o $@

$(BIN): $(OBJS)
	$(CC) $(OBJS) -o $@ $(LDFLAGS)

clean:
	rm -f bench-* *.o payload.inc

.PHONY: all clean
