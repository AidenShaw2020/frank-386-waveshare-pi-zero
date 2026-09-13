"""Actual ELF DMA terminal-count test; peripheral callback is simulated.

Tests controller state/port reads, not sound quality or hardware timing.
"""
import struct
import sys
from elftools.elf.elffile import ELFFile
from elf_harness import Firmware
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR

filename = sys.argv[1]
types = {}
with open(filename, 'rb') as stream:
    for cu in ELFFile(stream).get_dwarf_info().iter_CUs():
        for die in cu.iter_DIEs():
            name = die.attributes.get('DW_AT_name')
            if (die.tag == 'DW_TAG_structure_type' and name
                    and name.value in (b'I8257State', b'I8257Regs')
                    and 'DW_AT_byte_size' in die.attributes):
                types[name.value] = (die.attributes['DW_AT_byte_size'].value,
                                    Firmware.layout(die))
ds, d = types[b'I8257State']
rs, r = types[b'I8257Regs']
failures = []
for shift in (0, 1):
    for auto in (False, True):
        for terminal in (False, True):
            fw = Firmware(filename)
            fw.uc.mem_map(0x40000000, 0x100000)
            base = fw.CPU
            reg = base + d['regs'] + rs  # channel 1
            fw.uc.mem_write(base, bytes(ds))
            def u32(addr, value):
                fw.uc.mem_write(addr, struct.pack('<I', value))
            def u8(addr, value):
                fw.uc.mem_write(addr, bytes([value]))
            u32(base + d['dshift'], shift)
            u8(base + d['mask'], 0x0d)
            u8(base + d['status'], 0x20)
            u8(reg + r['mode'], 0x59 if auto else 0x49)
            fw.uc.mem_write(reg + r['base'], struct.pack('<HH', 0x72c7, 1311))
            u32(reg + r['now'], 0x72c7 << shift)
            callback = base + 0x1000
            u32(reg + r['transfer_handler'], callback | 1)
            length = 1312 << shift
            returned = length if terminal else length - (1 << shift)
            def transfer(uc, address, size, data):
                # Single-cycle DSP deasserts DREQ on completion even when
                # the *controller* has auto-init enabled (Teenagent).
                if terminal:
                    u8(base + d['status'], 0)
                uc.reg_write(UC_ARM_REG_R0, returned)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            fw.uc.hook_add(UC_HOOK_CODE, transfer, None, callback, callback)
            fw.call('i8257_dma_run', base)
            def port16(port):
                u8(base + d['flip_flop'], 0)
                return (fw.call('i8257_read_chan', base, port << shift, 1)
                        | fw.call('i8257_read_chan', base, port << shift, 1) << 8)
            count, address = port16(3), port16(2)
            expected_pos = 0 if terminal and auto else returned
            expected_count = (1311 - (expected_pos >> shift)) & 0xffff
            expected_addr = (0x72c7 + (expected_pos >> shift)) & 0xffff
            status = fw.uc.mem_read(base + d['status'], 1)[0]
            ok = (count == expected_count and address == expected_addr
                  and bool(status & 2) == terminal)
            label = f'shift={shift} auto={auto} terminal={terminal}'
            print(f'{label}: count={count:04x} address={address:04x} '
                  f'expected={expected_count:04x}/{expected_addr:04x} '
                  f'{"PASS" if ok else "FAIL"}')
            if not ok:
                failures.append(label)
            fw.close()
assert not failures, failures
