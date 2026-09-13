"""Run the ELF's real SB PCM consumer at ring boundaries (no hardware)."""
import sys
import struct
from elftools.elf.elffile import ELFFile
from elf_harness import Firmware


def layout(filename):
    with open(filename, 'rb') as stream:
        for cu in ELFFile(stream).get_dwarf_info().iter_CUs():
            for die in cu.iter_DIEs():
                name = die.attributes.get('DW_AT_name')
                if (die.tag == 'DW_TAG_structure_type' and name and
                        name.value == b'SB16State' and
                        'DW_AT_byte_size' in die.attributes):
                    return die.attributes['DW_AT_byte_size'].value, {
                        child.attributes['DW_AT_name'].value.decode():
                        child.attributes['DW_AT_data_member_location'].value
                        for child in die.iter_children()
                        if child.tag == 'DW_TAG_member'}
    raise RuntimeError('SB16State DWARF missing')


def read_out_hz(filename):
    with open(filename, 'rb') as stream:
        elf = ELFFile(stream)
        for section in elf.iter_sections():
            if section.header['sh_type'] != 'SHT_SYMTAB':
                continue
            for sym in section.iter_symbols():
                if sym.name == 'g_audio_out_hz':
                    addr = sym['st_value']
                    for sec in elf.iter_sections():
                        lo = sec.header['sh_addr']
                        hi = lo + sec.header['sh_size']
                        if sec.header['sh_type'] == 'SHT_PROGBITS' and lo <= addr < hi:
                            off = addr - lo
                            return struct.unpack_from('<I', sec.data(), off)[0]
    raise RuntimeError('g_audio_out_hz missing from ' + filename)


def main(filename):
    size, offsets = layout(filename)
    # The step is exactly 1.0 only when the guest rate equals the rate the
    # board plays at.  That is g_audio_out_hz, not a nominal 44100: the two
    # differed by 3.07% for as long as the timer period and the PIO divider
    # were set from different numbers.
    out_hz = read_out_hz(filename)
    count = 0
    for fmt in range(4):
        for stereo in (0, 1):
            for wrap in (False, True):
                fw = Firmware(filename)
                base = fw.CPU
                fw.uc.mem_write(base, bytes(size))
                def put(name, value):
                    fw.uc.mem_write(base + offsets[name], struct.pack('<I', value))
                width = 1 if fmt < 2 else 2
                frame = width * (1 + stereo)
                start = 4096 - frame if wrap else 0
                put('active_out', 1)
                put('freq', out_hz)
                put('fmt', fmt)
                put('fmt_stereo', stereo)
                put('audio_p', start)
                put('audio_q', start + frame)
                samples = (23, 71) if width == 1 else (1234, 2345)
                encoded = struct.pack('<BB' if width == 1 else '<HH', *samples)
                fw.uc.mem_write(base + offsets['audio_buf'], bytes([0xEE]) * 4096)
                fw.uc.mem_write(base + offsets['audio_buf'] + start, encoded[:frame])
                values = [((v - 128) * 256 if fmt == 0 else v * 256)
                          if width == 1 else (v - 32768 if fmt == 2 else v)
                          for v in samples]
                expected = (values[0], values[1] if stereo else values[0])
                out = base + 0x8000
                for iteration in range(4):
                    fw.uc.mem_write(out, bytes(8))
                    fw.call('sb16_getsample', base, out + 4, out)
                    actual = struct.unpack('<ii', fw.uc.mem_read(out, 8))
                    assert actual == expected, (fmt, stereo, wrap, iteration, actual, expected)
                    assert fw.read32(base + offsets['audio_p']) == start + frame
                # A new block must resume at its first sample, even after underrun.
                pos = (start + frame) % 4096
                fw.uc.mem_write(base + offsets['audio_buf'] + pos, encoded[:frame])
                put('audio_q', start + 2 * frame)
                fw.uc.mem_write(out, bytes(8))
                fw.call('sb16_getsample', base, out + 4, out)
                assert struct.unpack('<ii', fw.uc.mem_read(out, 8)) == expected
                count += 1
    print(f'PASS: {count} PCM format/stereo/wrap cases, final frame, underrun hold and restart')


if __name__ == '__main__':
    main(sys.argv[1])
