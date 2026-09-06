"""Execute the actual firmware interpreter and Thumb JIT on a host machine.

No SWD connection, firmware flash or substitute Python instruction emitter.
Addresses and structure layouts come from the supplied ELF, including DWARF.
This models CPU instructions, NOT RP2350 timing, peripherals or XIP coherence.
"""
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
    UC_CPU_ARM_CORTEX_M33,
)


def concrete(die):
    while die.tag in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"):
        die = die.get_DIE_from_attribute("DW_AT_type")
    return die


class Firmware:
    _images = {}
    CPU = 0x21000000
    TLB = 0x21001000
    STACK = 0x2101F000
    RETURN = 0x2101F800
    RAM = 0x11000000
    RAM_SIZE = 8 * 1024 * 1024
    START = 0x4000

    def __init__(self, filename):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M33)
        for addr, size in ((0x10000000, 0x800000), (self.RAM, self.RAM_SIZE),
                           (0x20000000, 0x82000), (self.CPU, 0x20000)):
            self.uc.mem_map(addr, size)
        key = (str(Path(filename).resolve()), Path(filename).stat().st_mtime_ns)
        if key in self._images:
            self.symbols, self.layouts, sections = self._images[key]
            for addr, data in sections:
                self.uc.mem_write(addr, data)
            self.install_libc()
            return
        self.symbols = {}
        self.layouts = {}
        sections = []
        with Path(filename).open("rb") as stream:
            elf = ELFFile(stream)
            for sec in elf.iter_sections():
                if sec["sh_flags"] & 2 and sec["sh_size"] and sec["sh_type"] != "SHT_NOBITS":
                    sections.append((sec["sh_addr"], sec.data()))
                    self.uc.mem_write(*sections[-1])
            for sym in elf.get_section_by_name(".symtab").iter_symbols():
                if sym["st_value"]:
                    self.symbols[sym.name] = sym["st_value"]
            for cu in elf.get_dwarf_info().iter_CUs():
                for die in cu.iter_DIEs():
                    name = die.attributes.get("DW_AT_name")
                    if name and name.value in (b"CPUI386", b"nj_block_t"):
                        typ = concrete(die)
                        if "DW_AT_byte_size" in typ.attributes:
                            self.layouts[name.value.decode()] = self.layout(typ)
        for needed in ("CPUI386", "nj_block_t"):
            if needed not in self.layouts:
                raise RuntimeError(f"ELF lacks DWARF layout for {needed}")
        self._images[key] = self.symbols, self.layouts, sections
        self.install_libc()

    def install_libc(self):
        # SDK libc can dispatch through boot-ROM function pointers initialized
        # at board boot. Replace only these C library services, not CPU/JIT code.
        self.hooks = []
        for name in ("memcpy", "memmove", "memset"):
            addr = self.symbols.get(name)
            if addr:
                self.hooks.append(self.uc.hook_add(UC_HOOK_CODE, self.libc, name, addr & ~1, addr & ~1))

    def close(self):
        for hook in self.hooks:
            self.uc.hook_del(hook)
        self.hooks.clear()

    @staticmethod
    def layout(typ, prefix="", offset=0):
        result = {}
        for member in typ.iter_children():
            if member.tag != "DW_TAG_member":
                continue
            name = member.attributes["DW_AT_name"].value.decode()
            loc = member.attributes["DW_AT_data_member_location"].value
            if not isinstance(loc, int):
                raise RuntimeError("Unsupported DWARF location expression")
            target = concrete(member.get_DIE_from_attribute("DW_AT_type"))
            key = prefix + name
            result[key] = offset + loc
            if target.tag == "DW_TAG_structure_type":
                result.update(Firmware.layout(target, key + ".", offset + loc))
        return result

    def libc(self, uc, address, size, name):
        dst, src, length = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
        if length > self.RAM_SIZE:
            raise RuntimeError(f"Unreasonable {name} length {length}")
        data = bytes([src & 255]) * length if name == "memset" else bytes(uc.mem_read(src, length))
        if length:
            uc.mem_write(dst, data)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def call(self, function, *args):
        addr = self.symbols[function] if isinstance(function, str) else function
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            self.uc.reg_write(reg, value)
        self.uc.reg_write(UC_ARM_REG_SP, self.STACK)
        self.uc.reg_write(UC_ARM_REG_LR, self.RETURN | 1)
        try:
            # Instruction cap bounds runaway code without creating a native
            # timeout thread for every tiny firmware function invocation.
            self.uc.emu_start(addr | 1, self.RETURN, count=2_000_000)
        except Exception as exc:
            pc = self.uc.reg_read(UC_ARM_REG_PC)
            raise RuntimeError(f"{function}: ARM PC={pc:08x}, bytes={bytes(self.uc.mem_read(pc, 8)).hex()}: {exc}") from exc
        if self.uc.reg_read(UC_ARM_REG_PC) != self.RETURN:
            raise RuntimeError(f"{function}: execution budget exhausted")
        return self.uc.reg_read(UC_ARM_REG_R0)

    def put(self, field, value, width=4):
        self.uc.mem_write(self.CPU + self.layouts["CPUI386"][field], value.to_bytes(width, "little"))

    def get(self, field):
        return self.read32(self.CPU + self.layouts["CPUI386"][field])

    def read32(self, addr):
        return struct.unpack("<I", self.uc.mem_read(addr, 4))[0]

    def prepare(self, code, registers, bits=32, flags=2):
        self.put("tlb.tab", self.TLB)
        self.put("tlb.size", 512)  # RP2350 target; ESP32 is not this harness.
        self.put("phys_mem", self.RAM)
        self.put("phys_mem_size", self.RAM_SIZE)
        self.put("a20_mask", 0xFFFFFFFF)
        self.put("gen", 3)
        self.put("flags_mask", 0x00037FD7)
        self.call("cpui386_reset_pm", self.CPU, self.START)
        if bits == 16:
            self.put("cr0", 0)
            self.put("code16", 1, 1)
            self.put("sp_mask", 0xFFFF)
        self.put("flags", flags)
        self.uc.mem_write(self.CPU + self.layouts["CPUI386"]["gprx"], struct.pack("<8I", *registers))
        self.uc.mem_write(self.RAM + self.START, code)
        self.call("njit_diag_reset_hot")

    def snapshot(self):
        self.call("refresh_flags", self.CPU)
        regs = struct.unpack("<8I", self.uc.mem_read(self.CPU + self.layouts["CPUI386"]["gprx"], 32))
        return {"registers": regs, "next_ip": self.get("next_ip"), "flags": self.get("flags")}

    def compile_block(self):
        """Compile at START and return the nj_block_t address, or 0."""
        # The real dispatcher establishes mode-specific memory trampolines
        # before entering a compiler; doing so mid-compile is unsafe.
        # The named wrapper is preferred: nj_guards_refresh itself vanishes
        # from the symbol table whenever the compiler inlines it into its only
        # caller, and without it every memory operand is refused here while
        # the firmware works perfectly.
        for name in ("njit_test_guards_refresh", "nj_guards_refresh"):
            if name in self.symbols:
                self.call(name, self.CPU)
                break
        return self.call("nj_compile_v6_trace", self.CPU, self.START)

    def block_field(self, block, name, width=4):
        offset = block + self.layouts["nj_block_t"][name]
        return int.from_bytes(self.uc.mem_read(offset, width), "little")

    def compile(self):
        block = self.compile_block()
        if not block:
            return None
        layout = self.layouts["nj_block_t"]
        return (self.read32(block + layout["code"]),
                self.uc.mem_read(block + layout["insns"], 1)[0])
