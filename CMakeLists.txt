# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.2.0)
set(toolchainVersion 14_2_Rel1)
set(picotoolVersion 2.2.0)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
cmake_minimum_required(VERSION 3.13)

# Board configuration variant (M1, M2, PC, Z2 or C2)
set(BOARD "M2" CACHE STRING "Board variant: M1, M2, PC, Z2 or C2")
# Alias for backwards compatibility
if(DEFINED BOARD_VARIANT AND NOT DEFINED BOARD)
    set(BOARD ${BOARD_VARIANT})
endif()

if(BOARD STREQUAL "Z2")
    # Waveshare RP2350-PiZero uses RP2350B and GPIO30..47.
    # The stock pico2 definition selects RP2350A and would truncate/alias
    # the high GPIOs used by HDMI, microSD and PSRAM CS.
    #
    # Pico SDK 2.2.0 (the version referenced by this project) predates the
    # upstream waveshare_rp2350_pizero board header, so keep a local one.
    set(PICO_BOARD_HEADER_DIRS ${CMAKE_CURRENT_LIST_DIR}/boards CACHE PATH "" FORCE)
    set(PICO_BOARD "frank_waveshare_rp2350_pizero" CACHE STRING "Pico board type" FORCE)
elseif(BOARD STREQUAL "C2")
    # FRANK Core 2 master is an RP2350B. The stock pico2 definition
    # declares PICO_RP2350A 1, capping NUM_BANK0_GPIOS at 30 and
    # switching off PIO's movable GPIO window — the link bus (GPIO20..42),
    # the WS2812 (GPIO46) and PSRAM CS (GPIO47) would alias down into
    # 0..29. Our own header sets PICO_RP2350A 0 / PICO_PIO_USE_GPIO_BASE 1.
    set(PICO_BOARD_HEADER_DIRS ${CMAKE_CURRENT_LIST_DIR}/boards CACHE PATH "" FORCE)
    set(PICO_BOARD "frank_core2_master" CACHE STRING "Pico board type" FORCE)
    # USB HID host is the only input path on C2 — no PS/2, no NES pad header.
    set(USB_HID_ENABLED ON CACHE BOOL "USB HID host" FORCE)
    # HDMI is the default video output; there is no analog VGA DAC to
    # auto-detect, so the pin probe is skipped either way. -DFORCE_VGA=ON
    # drives VGA timing out of GPIO12..19 instead, for bench debug.
    if(NOT FORCE_VGA)
        set(FORCE_HDMI ON CACHE BOOL "Force HDMI" FORCE)
    endif()
    # The TDA1387T I2S DAC sits on GPIO9..11 — the same pins the M2 PWM
    # path would drive. I2S is not optional here.
    set(AUDIO_TYPE "I2S" CACHE STRING "Audio output" FORCE)
else()
    # Set board to Pico 2 (RP2350)
    set(PICO_BOARD pico2 CACHE STRING "Pico board type")
endif()

set(BUILD_NAME "386")

# Firmware version string (set by release.sh or manually)
# FIRMWARE_VERSION is derived from version.txt after parsing (see below)

# CPU overclock configuration (252, 378, 504 MHz)
# 252 - very basic overclock, 378 = stable overclock, 504 = high overclock
set(CPU_SPEED "504" CACHE STRING "CPU clock in MHz: 378, 504")

# PSRAM speed configuration (133, 166 MHz target)
set(PSRAM_SPEED "66" CACHE STRING "PSRAM max frequency in MHz: 66, 84, 100, 133, 166")

# Audio output configuration (I2S or PWM)
set(AUDIO_TYPE "PWM" CACHE STRING "Audio output: I2S or PWM")

# Enable LTEMM support (performance degradation is expected)
option(EMM "Enable LTEMM support" OFF)

# USB HID keyboard support (disabled by default to keep USB CDC for debug)
option(USB_HID_ENABLED "Enable USB HID keyboard (disables USB CDC stdio)" ON)

# Force HDMI output (skip VGA/HDMI pin auto-detection)
option(FORCE_HDMI "Force HDMI output instead of VGA" OFF)

# Force VGA output (skip auto-detection). Takes precedence over FORCE_HDMI.
option(FORCE_VGA "Force VGA output instead of HDMI" OFF)

# Experimental HDMI path for 320x200x16 planar modes.  OFF renders directly
# from the internal SRAM VGA aperture; ON builds the older double-buffered
# chunky frame cache in PSRAM.
option(EGA320_PSRAM_CACHE "Use PSRAM framebuffer cache for HDMI EGA320" OFF)

# HDMI serializer source clock. 504 MHz is the normal fast build; 252 MHz
# reproduces the known-good PicoDVI clock regime for signal diagnostics.
set(HDMI_SYS_CLOCK_MHZ "504" CACHE STRING "System clock used by the HDMI serializer")

# Debug output (disabled by default for cleaner console)
option(DEBUG_ENABLED "Enable verbose debug output" OFF)

# Profiling (for performance analysis)
option(PROFILE_ENABLED "Enable i386 instruction profiling" OFF)

# Coarse per-subsystem cycle accounting on core 0 (docs/C2_SPLIT_PLAN.md).
# Distinct from PROFILE_ENABLED, which profiles instructions.
option(SUBSYS_PROFILE "Enable core-0 subsystem cycle profiling" OFF)

if(BOARD STREQUAL "M1")
    SET(BUILD_NAME "m1p2-${BUILD_NAME}")
elseif(BOARD STREQUAL "PC")
    SET(BUILD_NAME "PCp2-${BUILD_NAME}")
elseif(BOARD STREQUAL "Z2")
    SET(BUILD_NAME "z0p2-${BUILD_NAME}")
elseif(BOARD STREQUAL "C2")
    SET(BUILD_NAME "c2p2-${BUILD_NAME}")
else()  # M2
    SET(BUILD_NAME "m2p2-${BUILD_NAME}")
endif()

# CPU voltage selection based on speed
if(CPU_SPEED GREATER_EQUAL 504)
    set(CPU_VOLTAGE "VREG_VOLTAGE_1_65")
elseif(CPU_SPEED GREATER_EQUAL 378)
    set(CPU_VOLTAGE "VREG_VOLTAGE_1_60")
else()
    set(CPU_VOLTAGE "VREG_VOLTAGE_1_50")
endif()

message(STATUS "frank-386: Board=${BOARD}, CPU=${CPU_SPEED}MHz, PSRAM=${PSRAM_SPEED}MHz, Voltage=${CPU_VOLTAGE} ${AUDIO_TYPE}")

include(pico_sdk_import.cmake)

set(OUTPUT_DIR "${CMAKE_SOURCE_DIR}/bin/${CMAKE_BUILD_TYPE}")
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${OUTPUT_DIR}")

project(${BUILD_NAME} C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Read version from version.txt (format: "MAJOR MINOR" e.g. "1 00" = v1.00)
file(READ "${CMAKE_CURRENT_LIST_DIR}/version.txt" VERSION_CONTENT)
string(STRIP "${VERSION_CONTENT}" VERSION_CONTENT)
string(REPLACE " " ";" VERSION_LIST "${VERSION_CONTENT}")
list(GET VERSION_LIST 0 FRANK_386_VERSION_MAJOR)
list(GET VERSION_LIST 1 FRANK_386_VERSION_MINOR)
string(LENGTH "${FRANK_386_VERSION_MINOR}" _minor_len)
if(_minor_len LESS 2)
    set(FIRMWARE_VERSION "v${FRANK_386_VERSION_MAJOR}.0${FRANK_386_VERSION_MINOR}")
else()
    set(FIRMWARE_VERSION "v${FRANK_386_VERSION_MAJOR}.${FRANK_386_VERSION_MINOR}")
endif()
message(STATUS "frank-386 version: ${FIRMWARE_VERSION}")

pico_sdk_init()

#=============================================================================
# FatFS Library
#=============================================================================
add_library(fatfs
    drivers/fatfs/ff.c
    drivers/fatfs/ffsystem.c
    drivers/fatfs/ffunicode.c
    drivers/fatfs/f_util.c
)
target_include_directories(fatfs PUBLIC drivers/fatfs)
target_link_libraries(fatfs pico_stdlib)

#=============================================================================
# SD Card Driver
#=============================================================================
add_library(sdcard
    drivers/sdcard/sdcard.c
    drivers/sdcard/pio_spi.c
)
target_include_directories(sdcard PUBLIC drivers/sdcard drivers/fatfs src)
target_compile_definitions(sdcard PRIVATE
    BOARD_${BOARD}
    RP2350_BUILD=1
    # SDCARD_PIO=1  # Disabled - use hardware SPI instead
)
target_link_libraries(sdcard pico_stdlib hardware_spi hardware_pio hardware_dma)
pico_generate_pio_header(sdcard ${CMAKE_CURRENT_LIST_DIR}/drivers/sdcard/spi.pio)

#=============================================================================
# Unified PS/2 Driver (keyboard + mouse on shared PIO)
#=============================================================================
add_library(ps2_driver
    drivers/ps2/ps2.c
)
target_include_directories(ps2_driver PUBLIC drivers/ps2 src)
target_compile_definitions(ps2_driver PRIVATE
    BOARD_${BOARD}
)
target_link_libraries(ps2_driver pico_stdlib hardware_pio hardware_gpio hardware_clocks)
pico_generate_pio_header(ps2_driver ${CMAKE_CURRENT_LIST_DIR}/drivers/ps2/ps2.pio)

#=============================================================================
# NES/SNES Gamepad Driver
#=============================================================================
add_library(nespad
    drivers/nespad/nespad.c
)
target_include_directories(nespad PUBLIC drivers/nespad src)
target_compile_definitions(nespad PRIVATE
    BOARD_${BOARD}
)
target_link_libraries(nespad pico_stdlib hardware_pio hardware_gpio)

#=============================================================================
# PSRAM Driver
#=============================================================================
add_library(psram
    drivers/psram/psram_init.c
)
target_include_directories(psram PUBLIC drivers/psram src)
target_compile_definitions(psram PRIVATE
    BOARD_${BOARD}
    PSRAM_MAX_FREQ_MHZ=${PSRAM_SPEED}
)
target_link_libraries(psram pico_stdlib hardware_clocks)

#=============================================================================
# VGA Video Driver
#=============================================================================
add_library(video_driver
    drivers/hdmi/hdmi.c
    drivers/vga/test_pins.c
    drivers/vga/vga_hw.c
    drivers/vga/vga_osd.c
    drivers/vga/font8x16.c
)
target_include_directories(video_driver PUBLIC drivers/vga drivers/hdmi src)
target_compile_definitions(video_driver PRIVATE
    BOARD_${BOARD}
    RP2350_BUILD=1
    HDMI_SYS_CLOCK_MHZ=${HDMI_SYS_CLOCK_MHZ}
    # gfx_buffer is defined in this library, so the VGA size has to reach it
    # here; the main target's definitions do not.
    EMU_VGA_MEM_SIZE_KB=256
)
if(EGA320_PSRAM_CACHE)
    target_compile_definitions(video_driver PRIVATE EGA320_PSRAM_CACHE=1)
else()
    target_compile_definitions(video_driver PRIVATE EGA320_PSRAM_CACHE=0)
endif()
target_link_libraries(video_driver pico_stdlib hardware_dma hardware_pio hardware_clocks hardware_sync)

#=============================================================================
# I2S Audio Driver
#=============================================================================
add_library(audio_driver
    drivers/audio/audio.c
)
target_include_directories(audio_driver PUBLIC drivers/audio src)
target_compile_definitions(audio_driver PRIVATE
    BOARD_${BOARD}
)
pico_generate_pio_header(audio_driver ${CMAKE_CURRENT_LIST_DIR}/drivers/audio/audio_i2s.pio)
if(AUDIO_TYPE STREQUAL "I2S")
    target_link_libraries(audio_driver pico_stdlib hardware_dma hardware_pio hardware_clocks hardware_sync hardware_irq)
    target_compile_definitions(audio_driver PRIVATE FEATURE_AUDIO_I2S=1 SOUND_FREQUENCY=44100)
elseif(AUDIO_TYPE STREQUAL "PWM")
    target_link_libraries(audio_driver pico_stdlib hardware_dma hardware_pio hardware_clocks hardware_sync hardware_irq hardware_pwm)
    target_compile_definitions(audio_driver PRIVATE FEATURE_AUDIO_PWM=1 SOUND_FREQUENCY=44100)
endif()

#=============================================================================
# USB HID Keyboard Driver (optional)
#=============================================================================
add_subdirectory(drivers/usbhid)

if(EMM)
    SET(BUILD_NAME "${BUILD_NAME}-${CPU_SPEED}MHz-P${PSRAM_SPEED}-${AUDIO_TYPE}-${FIRMWARE_VERSION}-emm")
else ()
    SET(BUILD_NAME "${BUILD_NAME}-${CPU_SPEED}MHz-P${PSRAM_SPEED}-${AUDIO_TYPE}-${FIRMWARE_VERSION}")
endif()

#=============================================================================
# Core 386 Emulator Sources
#=============================================================================
set(EMU_CORE_SOURCES
    # CPU and core
    src/i386.c
    src/i386_arm.S   # ARM assembly optimizations for hot paths
    src/pc.c

    # Peripheral chips
    src/i8042.c      # Keyboard controller
    src/i8254.c      # Timer
    src/i8257.c      # DMA
    src/i8259.c      # PIC

    # Devices
    src/vga.c        # VGA emulation
    src/disk.c       # INT 13h disk handler (from pico-286)
    src/ide.c
    src/fdd.c
    src/pci.c        # PCI bus
    src/misc.c       # Serial, RTC, CMOS

    # Sound devices
    src/adlib.c      # Adlib/OPL2
    src/sb16.c       # Sound Blaster 16
    src/pcspk.c      # PC Speaker
    src/sn76489.c    # Tandy 3-Voice Sound (SN76489)
    src/dss.c        # Disney Sound Source
    # FM OPL synthesis engine
    src/emu8950/emu8950.c
    src/emu8950/emuadpcm.c
    src/emu8950/slot_render.cpp

    # Optional devices (can be disabled for size)
    src/fpu.c      # FPU emulation - disabled for initial port
    # src/ne2000.c   # Network - disabled for initial port

    # Configuration
    src/ini.c
    src/profile_subsys.c
    src/codeprofile.c
    src/gameport.c
    $<$<STREQUAL:${BOARD},C2>:${CMAKE_CURRENT_LIST_DIR}/src/diskcache.c>
    src/pcsample.c
    src/bbprofile.c
    src/autotype.c
    $<$<STREQUAL:${BOARD},C2>:${CMAKE_CURRENT_LIST_DIR}/src/remote_mem.c>
    $<$<STREQUAL:${BOARD},C2>:${CMAKE_CURRENT_LIST_DIR}/link/link_fast.c>

    # access to sd-card using W/A with network-like driver (mapdrive.com)
    src/netredirect.c
)

#=============================================================================
# Main Executable
#=============================================================================
add_executable(${BUILD_NAME}
    src/main.c
    src/platform_rp2350.c
    src/stubs_rp2350.c
    src/diskui.c
    src/settingsui.c
    src/config_save.c
    src/ps2kbd_mrmltr.cpp
    src/ps2kbd_wrapper.cpp
    ${EMU_CORE_SOURCES}
)

target_include_directories(${BUILD_NAME} PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/src
    ${CMAKE_CURRENT_LIST_DIR}/drivers/fatfs
    ${CMAKE_CURRENT_LIST_DIR}/drivers/sdcard
    ${CMAKE_CURRENT_LIST_DIR}/drivers/ps2
    ${CMAKE_CURRENT_LIST_DIR}/drivers/psram
    ${CMAKE_CURRENT_LIST_DIR}/drivers/vga
    ${CMAKE_CURRENT_LIST_DIR}/drivers/hdmi
    ${CMAKE_CURRENT_LIST_DIR}/drivers/audio
    ${CMAKE_CURRENT_LIST_DIR}/drivers/nespad
    ${CMAKE_CURRENT_LIST_DIR}/link
)
if(BOARD STREQUAL "C2")
    pico_generate_pio_header(${BUILD_NAME} ${CMAKE_CURRENT_LIST_DIR}/link/link_bus.pio)
endif()
# Only include usbhid directory when USB HID is enabled (to avoid tusb_config.h conflicts)
if(USB_HID_ENABLED)
    target_include_directories(${BUILD_NAME} PRIVATE
        ${CMAKE_CURRENT_LIST_DIR}/drivers/usbhid
    )
endif()

if(AUDIO_TYPE STREQUAL "I2S")
    target_compile_definitions(${BUILD_NAME} PRIVATE FEATURE_AUDIO_I2S=1)
elseif(AUDIO_TYPE STREQUAL "PWM")
    target_compile_definitions(${BUILD_NAME} PRIVATE FEATURE_AUDIO_PWM=1)
endif()

if(EMM)
    target_compile_definitions(${BUILD_NAME} PRIVATE EMULATE_LTEMS=1)
endif()
target_compile_definitions(${BUILD_NAME} PRIVATE
    # Version information
    FRANK_386_VERSION_MAJOR=${FRANK_386_VERSION_MAJOR}
    FRANK_386_VERSION_MINOR=${FRANK_386_VERSION_MINOR}

    # Board configuration
    BOARD_${BOARD}
    BOARD_VARIANT_STR="${BOARD}"
    CPU_CLOCK_MHZ=${CPU_SPEED}
    CPU_VOLTAGE=${CPU_VOLTAGE}
    PSRAM_MAX_FREQ_MHZ=${PSRAM_SPEED}

    # Platform identification
    PICO_ON_DEVICE=1
    RP2350_BUILD=1

    # Emulator configuration (8MB PSRAM)
    EMU_MEM_SIZE_MB=8
    # 256 KB is structural, not slack.  Planar modes are stored plane-
    # interleaved, four host bytes per guest byte, so 64 KB of guest VGA
    # address space needs 256 KB here; Wolf3D's three pages alone reach byte
    # 133120.  Halving it was tried on hardware and blacks out those modes.
    EMU_VGA_MEM_SIZE_KB=256
    EMU_CPU_GEN=4

    # Disable features for initial port
#    NO_FPU=1
    # NO_SOUND=1  # Sound enabled with I2S output
    NO_NETWORK=1

    # Performance optimizations
    I386_ENABLE_INLINE=1

    SOUND_FREQUENCY=44100

    # OPL2
    USE_EMU8950_OPL
    EMU8950_SLOT_RENDER=1
    EMU8950_NO_RATECONV=1
    EMU8950_NO_WAVE_TABLE_MAP=1
    EMU8950_NO_TLL=1
    EMU8950_NO_FLOAT=1
    EMU8950_NO_TIMER=1
    EMU8950_NO_TEST_FLAG=1
    EMU8950_SIMPLER_NOISE=1
    EMU8950_SHORT_NOISE_UPDATE_CHECK=1
    EMU8950_LINEAR_SKIP=1
    EMU8950_LINEAR_END_OF_NOTE_OPTIMIZATION
    EMU8950_NO_PERCUSSION_MODE=1
    EMU8950_LINEAR=1
)

# Add DEBUG_ENABLED if enabled
if(DEBUG_ENABLED)
    target_compile_definitions(${BUILD_NAME} PRIVATE DEBUG_ENABLED=1)
endif()

# Force HDMI output
if(FORCE_HDMI)
    target_compile_definitions(${BUILD_NAME} PRIVATE FORCE_HDMI=1)
    target_compile_definitions(video_driver PRIVATE FORCE_HDMI=1)
endif()

# Force VGA output (wins over FORCE_HDMI in vga_hw_init)
if(FORCE_VGA)
    target_compile_definitions(${BUILD_NAME} PRIVATE FORCE_VGA=1)
    target_compile_definitions(video_driver PRIVATE FORCE_VGA=1)
endif()

# Live audio-path instrumentation read out over SWD. See src/audiodiag.h.
# Costs no SRAM: the counters and trace rings sit in the PSRAM behind the
# guest's unused 0xA0000 VGA aperture.
option(AUDIO_DIAG "Trace OPL writes and IRQ0 delivery into PSRAM" OFF)
# The per-store watch and the per-instruction trail are the only parts that
# cost SRAM, and they cost about 8 KB - enough that the native JIT and the
# diagnostics cannot both fit. Turn this OFF to keep the rest of the
# instrumentation alongside the JIT.
option(AUDIO_DIAG_HOT "Include the per-store and per-instruction diag hooks" ON)

# Size of the native JIT translation cache, in KB. It lives in SRAM, so
# shrinking it is the cheapest way to buy room for the diagnostics: 8 KB
# gives a comfortable margin over the ~42.7 KB heap that pc_new() needs,
# at the cost of translating hot code more often. Leave it at 16 for
# release builds.
set(NJIT_CODE_KB "16" CACHE STRING "Native JIT code cache size in KB")
target_compile_definitions(${BUILD_NAME} PRIVATE NJ_CODE_BYTES=${NJIT_CODE_KB}*1024u)

# Keep the tiny OPL per-sample kernels in main SRAM. They are called once per
# active operator per sample; leaving them in XIP flash makes them contend with
# guest-memory traffic for the flash cache. This costs about 2 KB of SRAM, so
# it is independently switchable for measured A/B builds.
option(OPL_KERNELS_RAM "Place OPL per-sample kernels in main SRAM" OFF)
if(OPL_KERNELS_RAM)
    target_compile_definitions(${BUILD_NAME} PRIVATE OPL_KERNELS_RAM=1)
endif()

# Low-risk subset of OPL_KERNELS_RAM: the profiler's hottest 92-byte kernel
# fits below the next 4 KB heap boundary, unlike all sixteen wrappers. Two
# clean Doom runs measured 166.0/166.2 s versus the 168.1 s prior average.
option(OPL_HOT_KERNEL_RAM "Place the hottest OPL kernel in main SRAM" ON)
if(OPL_HOT_KERNEL_RAM)
    target_compile_definitions(${BUILD_NAME} PRIVATE OPL_HOT_KERNEL_RAM=1)
endif()

# Record, per display line, the VRAM address the EGA renderer computes.
# Costs one store per scanline and lands in the unused port histogram at
# guest 0xb5000; on for chasing addressing bugs, off otherwise.
option(HDMI_ADDR_LOG "Log the per-scanline VRAM address" OFF)
if(HDMI_ADDR_LOG)
    target_compile_definitions(${BUILD_NAME} PRIVATE HDMI_ADDR_LOG_ENABLED=1)
endif()
if(AUDIO_DIAG)
    target_compile_definitions(${BUILD_NAME} PRIVATE FRANK_AUDIO_DIAG=1)
    if(NOT AUDIO_DIAG_HOT)
        target_compile_definitions(${BUILD_NAME} PRIVATE FRANK_DIAG_HOT=0)
    endif()
endif()

# Add I386_PROFILE if profiling enabled
if(PROFILE_ENABLED)
    target_compile_definitions(${BUILD_NAME} PRIVATE I386_PROFILE=1)
endif()

# Guest memory served by the slave RP2350 (C2 only). See src/remote_mem.h.
option(REMOTE_MEM "C2: serve a window of guest RAM from the slave's SRAM" OFF)

# Ignore cpu_freq/psram_freq from the SD card's config.ini. Needed for any
# clock-scaling measurement, since config.ini otherwise pins this board to
# 504 MHz regardless of what the build asked for.
option(PIN_CLOCKS "Ignore config.ini clock settings" OFF)
option(NO_NESPAD "Do not touch the NES gamepad GPIOs (GPIO4/6/7 on Z2)" OFF)
option(NO_BOD "Diagnostic: disable the brownout detector" OFF)
option(UART_CONSOLE "Z2: stdio on UART0 (GPIO4/5) via the debug probe" OFF)

# Guest code footprint measurement, for JIT cache sizing. See codeprofile.h.
option(CODE_PROFILE "Measure the guest code working set" OFF)

# Statistical PC sampling of core 0 via SysTick. See src/pcsample.c.
# Scripted keystrokes for unattended benchmarking. See src/autotype.h.
set(AUTOTYPE "" CACHE STRING "Keys to type into the guest after boot")
if(NOT AUTOTYPE STREQUAL "")
    target_compile_definitions(${BUILD_NAME} PRIVATE AUTOTYPE_STR="${AUTOTYPE}")
endif()

option(PC_SAMPLE "Sample core-0 PC to find interpreter hot spots" OFF)
set(PC_SAMPLE_SHIFT "11" CACHE STRING
    "RAM PC-sampling bucket shift (11=2KB/512B, 7=128B/8KB, 5=32B/32KB histogram)")

option(NATIVE_JIT "Experimental native Thumb-2 hot-block JIT" OFF)

# 8-bit operand forms in the native emitter (MOV r8 / imm8, register forms).
# OFF restores the emitter coverage that shipped before 2026-09-02.
# 8-bit operand forms in the native emitter. Verified correct on hardware and
# they cut the emitter's refusal rate by about three quarters, but they buy no
# throughput while the block cache is too small to keep what it compiles, so
# the default is off until that is fixed and the gain can be measured.
option(NJIT_OP8 "Native JIT: admit 8-bit register MOV forms" OFF)
if(NOT NJIT_OP8)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_OP8=0)
endif()

# Larger compiled-block cache (64 blocks) and hot table (64 slots). Costs 4992
# bytes of SRAM and DOES NOT BOOT on this configuration - 92.70% SRAM, black
# screen, pc_step() never runs. Needs SRAM freed elsewhere first.
# Measured on hardware: pc_new() needs a hair over 42728 bytes of heap and
# gets 43560 in the shipping build, so the whole margin is under 832 bytes.
# 64 blocks + 64 hot slots costs 3712 bytes against the 4160 freed by the
# smaller emu8950 scratch and the JIT diagnostics, so it lands 448 bytes
# BELOW the footprint that is known to boot. 128 hot slots does not: that
# was tried and stopped at DIAG_PRE_PC_NEW with a black screen.
# How a loop gets noticed at all; see NJ_DISCOVERY_MASK in i386.c.  The hot
# table is 2^NJIT_HOT_BITS slots of 20 bytes.
set(NJIT_HOT_BITS "5" CACHE STRING "log2 of the hot-backedge table size")
# Measured on hardware with 16-bit protected mode compiling (see FRANK_NJIT_PM16):
# a 63 mask with threshold 3 and the keyless counters gives Tyrian 2000 2.458
# MIPS against 2.391 with 15/2/off, and Draci historie 2.681 against 2.656.
set(NJIT_DISCOVERY_MASK "63" CACHE STRING
    "sample one uncompiled backedge in this many plus one")
set(NJIT_HOT_THRESHOLD "3" CACHE STRING
    "extra sightings a backedge needs before it is compiled")
# Keyless hot counters; see FRANK_NJIT_HOT_COUNTERS in i386.c.  Needs
# NJIT_INLINE_ENTRY off for the 4 KB.
# 16-bit protected mode in the trace compiler; see FRANK_NJIT_PM16 in i386.c.
# Measurement only: silence the periodic OPL refill to read the ceiling of
# moving it off core 0.  Never ship a build with this on.
option(FRANK_NO_ADLIB "Skip the periodic AdLib refill (measurement build)" OFF)
# OPL synthesis on core 1, in the gap where it waits for the I2S DMA.
# See FRANK_ADLIB_CORE1 in src/adlib.c.
option(ADLIB_CORE1 "Synthesise the OPL on core 1" OFF)
if(ADLIB_CORE1)
    # The synthesis runs as the I2S driver's idle hook, inside the wait for
    # the frame in flight.  A PWM board never calls repeat_me_often() into
    # i2s_dma_write(), so the hook would never run and the OPL would be
    # silent - a failure that looks like a missing sound card rather than a
    # misconfigured build.  Refuse it here instead.
    if(NOT AUDIO_TYPE STREQUAL "I2S")
        message(FATAL_ERROR
                "ADLIB_CORE1 needs AUDIO_TYPE=I2S: the OPL is pumped from the "
                "I2S DMA wait, and a PWM build would produce silence.")
    endif()
    target_compile_definitions(${BUILD_NAME} PRIVATE ADLIB_CORE1=1)
endif()
if(FRANK_NO_ADLIB)
    target_compile_definitions(${BUILD_NAME} PRIVATE FRANK_NO_ADLIB=1)
endif()
option(NJIT_PM16 "Compile 16-bit protected-mode traces" ON)
if(NOT NJIT_PM16)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_PM16=0)
endif()
option(NJIT_HOT_COUNTERS "Hotness by a keyless counter array" ON)
set(NJIT_HOT_CTR_BITS "12" CACHE STRING "log2 of the hot counter array")
if(NJIT_HOT_COUNTERS)
    target_compile_definitions(${BUILD_NAME} PRIVATE
                               NJIT_HOT_COUNTERS=1
                               NJ_HOT_CTR_BITS=${NJIT_HOT_CTR_BITS}u)
endif()
target_compile_definitions(${BUILD_NAME} PRIVATE
                           NJ_HOT_BITS=${NJIT_HOT_BITS}u
                           NJ_DISCOVERY_MASK=${NJIT_DISCOVERY_MASK}u
                           NJ_HOT_THRESHOLD=${NJIT_HOT_THRESHOLD}u)

option(NJIT_BIG_CACHE "Native JIT: 64-block cache and 64-slot hot table" OFF)
if(NJIT_BIG_CACHE)
    target_compile_definitions(${BUILD_NAME} PRIVATE
                               NJ_CACHE_SET_BITS=5u NJ_HOT_BITS=6u)
endif()

# Per-entry diagnostics in the JIT tables, for the profiler hotkeys. Costs
# 32 bytes per hot-table slot and 8 per cache block. See NJIT_DIAG in i386.c.
# Park in the fault handler instead of rebooting, so the stack survives for
# SWD to walk. Diagnostic only - a parked board looks dead to the user.
option(FAULT_PARK "Hard fault parks instead of rebooting" OFF)
if(FAULT_PARK)
    target_compile_definitions(${BUILD_NAME} PRIVATE FAULT_PARK=1)
endif()

option(NJIT_DIAG "Native JIT: per-entry diagnostic snapshots" OFF)
if(NJIT_DIAG)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_DIAG=1)
endif()

option(BLOCK_JIT "Experimental hot-block dynamic translator" OFF)

# Basic-block discovery and reuse profiling. Stage 1 of the JIT.
# SD block cache in the slave RP2350's PSRAM (C2 only). See src/diskcache.h.
# Inline the instruction-fetch fast path. Worth +5.6% but costs ~12 KB of
# RAM, so it is on only for the board it was measured and boot-tested on.
if(BOARD STREQUAL "C2")
    option(FAST_FETCH "Inline the instruction-fetch fast path" ON)
else()
    option(FAST_FETCH "Inline the instruction-fetch fast path" OFF)
endif()
if(FAST_FETCH)
    target_compile_definitions(${BUILD_NAME} PRIVATE FAST_FETCH=1)
endif()

option(DISK_CACHE "C2: cache SD blocks in the slave's PSRAM" OFF)
if(DISK_CACHE AND BOARD STREQUAL "C2")
    target_compile_definitions(${BUILD_NAME} PRIVATE DISK_CACHE=1)
    target_compile_definitions(sdcard PRIVATE DISK_CACHE=1)
endif()

option(BB_PROFILE "Profile guest basic blocks" OFF)
if(BB_PROFILE)
    target_compile_definitions(${BUILD_NAME} PRIVATE BB_PROFILE=1)
endif()
# Floor for the XIP flash clock, applied over whatever config.ini asks for.
# See set_flash_timings() for the measurement that motivates it.
if(FLASH_FREQ_FLOOR)
    target_compile_definitions(${BUILD_NAME} PRIVATE FLASH_FREQ_FLOOR_MHZ=${FLASH_FREQ_FLOOR})
endif()
if(DEFINED FLASH_RXDELAY)
    target_compile_definitions(${BUILD_NAME} PRIVATE FLASH_RXDELAY_OVERRIDE=${FLASH_RXDELAY})
endif()

# Probe the block cache every N interpreted instructions, not only at taken
# backward branches.  0 disables it.  See nj_probe_existing().
set(NJIT_PERIODIC_PROBE "0" CACHE STRING
    "Interpreted instructions between lookup-only JIT probes (0 = off)")
if(NOT NJIT_PERIODIC_PROBE STREQUAL "0")
    target_compile_definitions(${BUILD_NAME} PRIVATE
                               NJIT_PERIODIC_PROBE=${NJIT_PERIODIC_PROBE})
endif()

# Chains may compile a branch target while they are retiring fewer than this
# many guest instructions.  0 disables the speculation.  Worth +139% on Doom's
# renderer and it starves the OPL unless I2S_RING_FRAMES gives core 1 slack.
set(NJ_CHAIN_COMPILE_BELOW "0" CACHE STRING "Speculate below this chain length (0 = off)")
if(NOT NJ_CHAIN_COMPILE_BELOW STREQUAL "0")
    target_compile_definitions(${BUILD_NAME} PRIVATE
                               NJ_CHAIN_COMPILE_BELOW=${NJ_CHAIN_COMPILE_BELOW})
endif()

# Frames in the free-running I2S ring.  0 keeps the single-frame DMA that
# needs core 1 back every 22.7 us.  A power of two; 64 is 1.45 ms of slack.
set(I2S_RING_FRAMES "64" CACHE STRING "Frames in the free-running I2S DMA ring (0 = off)")
if(NOT I2S_RING_FRAMES STREQUAL "0")
    # audio.c is compiled into the audio_driver library, so a definition on
    # ${BUILD_NAME} alone never reaches it - the first attempt at this silently
    # built the old path and the counters gave it away by still advancing.
    target_compile_definitions(audio_driver PRIVATE I2S_RING_FRAMES=${I2S_RING_FRAMES})
    target_compile_definitions(${BUILD_NAME} PRIVATE I2S_RING_FRAMES=${I2S_RING_FRAMES})
endif()

# Measurement only: treat the VGA aperture as ordinary RAM in the generated
# guard, so the cost of side-exiting on it can be measured.  The display stops
# updating.  Never ship it.
option(NJIT_VGA_UNSAFE "Measurement: no VGA aperture side exit (breaks video)" OFF)
if(NJIT_VGA_UNSAFE)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_VGA_UNSAFE=1)
endif()

# Let a chain compile the target of a branch it exited through, instead of
# stopping when no block exists there.  Measured at -12% on Tyrian with a
# 32-block cache; Doom's chains are 1.11 blocks and may want it.
option(NJIT_CHAIN_COMPILE "Chains may compile branch targets" OFF)
if(NJIT_CHAIN_COMPILE)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_CHAIN_COMPILE=1)
endif()

# Count why native chains stop, for chainstat.py.  These are volatile
# read-modify-writes on the per-block path - three of them for every block of
# every chain - so they are off unless a measurement asks for them.
option(NJIT_CHAINSTAT "Count why native chains stop (chainstat.py)" OFF)
if(NJIT_CHAINSTAT)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_CHAINSTAT=1)
endif()

# Record the last sixteen native block exits in PSRAM; see FRANK_NJIT_EXIT_RING.
# Note the ring lives in the guest's physical memory hole, which pc_new()
# clears on every boot - so it is only readable with FAULT_PARK=ON, where the
# board stops in the fault instead of rebooting over the evidence.
if(NJIT_EXIT_RING)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_EXIT_RING=1)
endif()

# Verify each block leaves a fetchable next_ip; see FRANK_NJIT_EXIT_CHECK.
if(NJIT_EXIT_CHECK)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_EXIT_CHECK=1)
endif()

# SD card SPI clock in MHz.  The card clock only pays for the bytes, so this
# matters most together with the read-ahead run in drivers/sdcard/sdcard.c.
# Sequential read-ahead for the guest's disk image.  Worth a great deal when
# the card's filesystem makes FatFS read one sector at a time - measured at
# 25.6% of core 0 on a FAT32 card with a small cluster, halved by this - and
# worth about 1% on exFAT, where disk_read is 1.5% to begin with.  It needs
# 4 KB of SRAM, which only exists if the JIT entry is not inlined, and that
# costs 1.9%; hence the two move together and default off.
option(SD_READAHEAD "SD read-ahead; needs NJIT_INLINE_ENTRY off" OFF)

# Inlining nj_try_execute() at every backward branch in the interpreter costs
# 8 KB of static SRAM - the largest single block available on a board whose
# heap headroom is under a kilobyte - and buys 1.9%.  Turning it off is how
# anything else gets room: the SD read-ahead buffer, or NJIT_BIG_CACHE.
option(NJIT_INLINE_ENTRY "Inline the JIT entry (8 KB of SRAM for 1.9%)" ON)
if(NOT NJIT_INLINE_ENTRY)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_INLINE_ENTRY=0)
endif()
if(SD_READAHEAD)
    if(NJIT_INLINE_ENTRY)
        message(FATAL_ERROR
                "SD_READAHEAD needs -DNJIT_INLINE_ENTRY=OFF for its buffer")
    endif()
    target_compile_definitions(${BUILD_NAME} PRIVATE SDCARD_READAHEAD=1)
endif()

set(SDCARD_CLK_MHZ "30" CACHE STRING "SD card SPI clock in MHz")
# 0 leaves clk_peri where the SDK parks it (48 MHz from the USB PLL), which
# caps the SD card at 24 MHz however high SDCARD_CLK_MHZ is set.
set(PERI_CLOCK_MHZ "0" CACHE STRING "Peripheral clock in MHz, 0 = SDK default")
target_compile_definitions(${BUILD_NAME} PRIVATE PERI_CLOCK_MHZ=${PERI_CLOCK_MHZ})
target_compile_definitions(${BUILD_NAME} PRIVATE SDCARD_CLK_MHZ=${SDCARD_CLK_MHZ})

# Cycle-exact top-level split of core 0; see src/njit_timeprof.h.
option(NJIT_TIMEPROF "Measure where core 0 goes with the DWT cycle counter" OFF)
if(NJIT_TIMEPROF)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_TIMEPROF=1)
endif()

# Segment loads inside a trace; see FRANK_NJIT_SEG_LOAD.  OFF: it is fast and
# passes every host test, and it hangs M602 at random on the board.
set(NJIT_SEG_LOAD "7" CACHE STRING
    "Native JIT segment loads: 1=MOV r/m,Sreg 2=MOV Sreg,r/m 4=LES/LDS")
target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_SEG_LOAD=${NJIT_SEG_LOAD})

# Direct native access to the chain-4 VGA aperture; see FRANK_NJIT_VGA_DIRECT.
option(NJIT_VGA_DIRECT "Native JIT: direct access to a linear VGA aperture" ON)
if(NOT NJIT_VGA_DIRECT)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_VGA_DIRECT=0)
endif()

# Which memory-guard test refuses a native access; see FRANK_NJIT_GUARD_DIAG.
option(NJIT_GUARD_DIAG "Native JIT: count memory-guard refusals by reason" OFF)
if(NJIT_GUARD_DIAG)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_GUARD_DIAG=1)
endif()

# Out-of-line memory guard for the JIT; see FRANK_NJIT_SHARED_GUARD.
if(NJIT_SHARED_GUARD)
    target_compile_definitions(${BUILD_NAME} PRIVATE NJIT_SHARED_GUARD=1)
endif()

if(PC_SAMPLE)
    target_compile_definitions(${BUILD_NAME} PRIVATE
                               PC_SAMPLE=1
                               PC_SAMPLE_SHIFT=${PC_SAMPLE_SHIFT})
    # Aim the same 512 buckets at one region.  Empty means the default window.
    if(PS_BASE)
        target_compile_definitions(${BUILD_NAME} PRIVATE PS_BASE=${PS_BASE})
    endif()
    if(PS_SPAN)
        target_compile_definitions(${BUILD_NAME} PRIVATE PS_SPAN=${PS_SPAN})
    endif()
    # Aim the same 512 buckets at the flash text instead of the RAM text.
    # Samples outside the window are counted but not placed, and on this board
    # 28% of them landed outside - i.e. in code running from flash.
    if(PS_WINDOW_FLASH)
        target_compile_definitions(${BUILD_NAME} PRIVATE PS_WINDOW_FLASH=1)
    endif()
endif()
if(NATIVE_JIT)
    target_compile_definitions(${BUILD_NAME} PRIVATE NATIVE_JIT=1)
endif()
if(BLOCK_JIT)
    target_compile_definitions(${BUILD_NAME} PRIVATE BLOCK_JIT=1)
endif()
if(CODE_PROFILE)
    target_compile_definitions(${BUILD_NAME} PRIVATE CODE_PROFILE=1)
endif()
if(PIN_CLOCKS)
    target_compile_definitions(${BUILD_NAME} PRIVATE PIN_CLOCKS=1)
endif()
if(NO_BOD)
    target_compile_definitions(${BUILD_NAME} PRIVATE NO_BOD=1)
endif()
if(NO_NESPAD)
    target_compile_definitions(${BUILD_NAME} PRIVATE NO_NESPAD=1)
endif()
if(REMOTE_MEM AND BOARD STREQUAL "C2")
    target_compile_definitions(${BUILD_NAME} PRIVATE REMOTE_MEM=1)
endif()

# Subsystem cycle profiling. sdcard needs it too: the timed disk_read /
# disk_write wrappers live there.
if(SUBSYS_PROFILE)
    target_compile_definitions(${BUILD_NAME} PRIVATE SUBSYS_PROFILE=1)
    target_compile_definitions(sdcard PRIVATE SUBSYS_PROFILE=1)
endif()

# Compiler optimizations for performance
target_compile_options(${BUILD_NAME} PRIVATE
    -O3
    -ffunction-sections
    -fdata-sections
)

# ARM assembly flags for Cortex-M33
set_source_files_properties(src/i386_arm.S PROPERTIES
    COMPILE_FLAGS "-mcpu=cortex-m33 -mthumb"
)

target_link_libraries(${BUILD_NAME}
    pico_stdlib
    pico_multicore
    hardware_spi
    hardware_dma
    hardware_pio
    hardware_clocks
    hardware_vreg
    hardware_flash
    hardware_watchdog
    # PC_SAMPLE installs its SysTick handler at runtime rather than by
    # overriding the linked isr_systick vector, so the profiler build boots
    # identically to the shipping one until Win+F10 arms it.
    hardware_exception
    hardware_irq
    hardware_interp
    # For the on-die temperature sensor on ADC input 4. The board resets with
    # POWMAN_CHIP_RESET reporting HAD_BOR, and a hotter die draws more current
    # from an already marginal internal regulator, so the die temperature is
    # the one remaining quantity nobody has measured.
    hardware_adc
    fatfs
    sdcard
    ps2_driver
    psram
    video_driver
    audio_driver
    nespad
    usbhid
)

# USB configuration: USB HID uses Host mode which disables CDC
if(USB_HID_ENABLED)
    pico_enable_stdio_usb(${BUILD_NAME} 0)
    if(BOARD STREQUAL "C2")
        # C2 has a dedicated debug UART header (J2: GPIO0 TX, GPIO1 RX,
        # 115200). With USB in host mode it is the only console there is,
        # and bring-up on a board whose slave cannot be reset by the
        # master is not something to attempt blind.
        message(STATUS "USB HID enabled: console on UART0 (J2, GPIO0/1)")
        pico_enable_stdio_uart(${BUILD_NAME} 1)
    elseif(UART_CONSOLE)
        # Z2 has no free UART by default - GPIO0/1 carry PS/2 and GPIO4/5 are
        # the NES pad's clock and the board header's UART pins at once. With
        # -DNO_NESPAD=ON the pad releases GPIO4/5, and this rig has the Debug
        # Probe's UART already cross-wired to them (board GPIO4 -> probe GP5,
        # board GPIO5 -> probe GP4), so the probe's CDC bridge becomes a real
        # console without giving up USB HID.
        #
        # That matters because the alternative, USB_HID_ENABLED=OFF, swaps the
        # board's USB from Host to Device and changes far more than logging.
        message(STATUS "USB HID enabled: console on UART0 (GPIO4/5, needs NO_NESPAD)")
        pico_enable_stdio_uart(${BUILD_NAME} 1)
    else()
        # Other boards route GPIO0/1 to PS/2 or a gamepad, so there is no
        # UART to fall back on — stdio is simply off.
        message(STATUS "USB HID enabled: no console (USB is Host mode for keyboard)")
        pico_enable_stdio_uart(${BUILD_NAME} 0)
    endif()
else()
    # USB Serial console (when USB HID is disabled)
    pico_enable_stdio_usb(${BUILD_NAME} 1)
    pico_enable_stdio_uart(${BUILD_NAME} 0)
endif()

#=============================================================================
# Print configuration summary
#=============================================================================
message(STATUS "")
message(STATUS "frank-386 Build Configuration:")
message(STATUS "  Board Variant:  ${BOARD}")
message(STATUS "  EMM:            ${EMM}")
message(STATUS "  CPU Speed:      ${CPU_SPEED} MHz")
message(STATUS "  CPU Voltage:    ${CPU_VOLTAGE}")
message(STATUS "  PSRAM Speed:    ${PSRAM_SPEED} MHz")
message(STATUS "  Audio Type:     ${AUDIO_TYPE}")
message(STATUS "  USB HID:        ${USB_HID_ENABLED}")
message(STATUS "  Debug Output:   ${DEBUG_ENABLED}")
message(STATUS "  Build Name:     ${BUILD_NAME}")
message(STATUS "")

# Create UF2 output
pico_add_extra_outputs(${BUILD_NAME})

target_link_options(${BUILD_NAME} PRIVATE -Xlinker --print-memory-usage --data-sections -Wl,--gc-sections
                    # so __wrap_abort() can record who called it; see main.c
                    -Wl,--wrap=abort)
