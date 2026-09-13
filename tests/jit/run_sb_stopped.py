"""A stale refill request must not restart stopped SB DMA. Run actual ELF."""
import sys, struct
from run_sb_pcm import layout
from elf_harness import Firmware
from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR

filename = sys.argv[1]
size, offsets = layout(filename)
with open(filename,'rb') as stream:
    for cu in ELFFile(stream).get_dwarf_info().iter_CUs():
        for die in cu.iter_DIEs():
            name=die.attributes.get('DW_AT_name')
            if die.tag=='DW_TAG_structure_type' and name and name.value==b'I8257State' and 'DW_AT_byte_size' in die.attributes:
                dma_offsets=Firmware.layout(die)
for running in (0, 1):
    fw = Firmware(filename)
    base = fw.CPU
    fw.uc.mem_write(base, bytes(size))
    fw.uc.mem_map(0x40000000,0x100000)
    dma=base+0x8000
    fw.uc.mem_write(dma,bytes(512))
    fw.uc.mem_write(dma+dma_offsets['phys_mem'],struct.pack('<I',fw.RAM))
    fw.uc.mem_write(dma+dma_offsets['phys_mem_size'],struct.pack('<I',fw.RAM_SIZE))
    def put(k, v):
        fw.uc.mem_write(base + offsets[k], struct.pack('<I', v))
    for k,v in dict(dma_running=running, voice=base, block_size=4096,
                    left_till_irq=4096, bytes_per_second=11236,
                    freq=11236, dma=1, isa_dma=dma, audio_p=0, audio_q=0).items():
        put(k,v)
    reads=[]
    def dma_read(uc, addr, length, user):
        reads.append(True)
        uc.reg_write(UC_ARM_REG_R0,0)
        uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
    addr=fw.symbols['write_audio'] & ~1
    fw.uc.hook_add(UC_HOOK_CODE,dma_read,None,addr,addr)
    before=bytes(fw.uc.mem_read(base,size))
    pos=fw.call('SB_read_DMA',base,1,17,4096)
    if not running:
        assert not reads, 'Stopped DMA attempted write_audio'
        assert pos==17 and bytes(fw.uc.mem_read(base,size))==before
    else:
        assert reads, 'Active DMA failed to dispatch write_audio'
print('PASS stopped DMA unchanged; active DMA still dispatches refill')
