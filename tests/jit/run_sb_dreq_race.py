"""Force a core-1 stale DREQ after core 0 stopped a single-cycle block.

Runs actual ELF functions; forced interleave proves possibility, not how
often it occurs on the board. write_audio is mocked to isolate dispatch.
"""
import struct, sys
from elftools.elf.elffile import ELFFile
from elf_harness import Firmware
from run_sb_pcm import layout
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R2, UC_ARM_REG_PC, UC_ARM_REG_LR

filename=sys.argv[1]
size,s=layout(filename)
types={}
with open(filename,'rb') as f:
    for cu in ELFFile(f).get_dwarf_info().iter_CUs():
        for die in cu.iter_DIEs():
            n=die.attributes.get('DW_AT_name')
            if die.tag=='DW_TAG_structure_type' and n and n.value in (b'I8257State',b'I8257Regs') and 'DW_AT_byte_size' in die.attributes:
                types[n.value]=(die.attributes['DW_AT_byte_size'].value,Firmware.layout(die))
fw=Firmware(filename)
fw.uc.mem_map(0x40000000,0x100000)
base=fw.CPU; dma=base+0x8000
ds,d=types[b'I8257State']; rs,r=types[b'I8257Regs']
reg=dma+d['regs']+rs
def put(addr,v): fw.uc.mem_write(addr,struct.pack('<I',v))
def byte(addr,v): fw.uc.mem_write(addr,bytes([v]))
fw.uc.mem_write(base,bytes(size)); fw.uc.mem_write(dma,bytes(ds))
for k,v in dict(active_out=1,dma_running=1,voice=base,block_size=1312,
                left_till_irq=1312,freq=11236,bytes_per_second=11236,
                dma=1,isa_dma=dma,audio_p=0,audio_q=8).items(): put(base+s[k],v)
fw.uc.mem_write(base+s['audio_buf'],bytes([128])*4096)
byte(dma+d['mask'],13); byte(dma+d['status'],0x20)
byte(reg+r['mode'],0x59)
fw.uc.mem_write(reg+r['base'],struct.pack('<HH',0x72c7,1311))
put(reg+r['now'],0x72c7)
put(reg+r['transfer_handler'],fw.symbols['SB_read_DMA']|1)
put(reg+r['opaque'],base)
interleaved=[]
def late_stop(uc,addr,length,user):
    # Consumer already evaluated dma_running=true, but before asserting
    # DREQ core 0 completes a block and deasserts the request.
    put(base+s['dma_running'],0); put(base+s['active_out'],0)
    put(base+s['irq_await_play'],1); put(base+s['irq_at_q'],8)
    byte(dma+d['status'],2)  # TC set, DREQ clear
    interleaved.append(True)
addr=fw.symbols['i8257_dma_hold_DREQ']&~1
hook=fw.uc.hook_add(UC_HOOK_CODE,late_stop,None,addr,addr)
out=base+0x9000
fw.uc.mem_write(out,bytes(8))
fw.call('sb16_getsample',base,out,out+4)
fw.uc.hook_del(hook)
if not interleaved:
    # Fixed consumer never accesses the DMA request. Complete the block
    # from core 0 here, then verify no request remains.
    late_stop(None,0,0,None)
reads=[]
def write_audio(uc,addr,length,user):
    reads.append(uc.reg_read(UC_ARM_REG_R2))
    uc.reg_write(UC_ARM_REG_R0,0)
    uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
addr=fw.symbols['write_audio']&~1
fw.uc.hook_add(UC_HOOK_CODE,write_audio,None,addr,addr)
status=fw.uc.mem_read(dma+d['status'],1)[0]
fw.call('i8257_dma_run',dma)
print(f'Stopped DSP: DMA status={status:02x}; unauthorized refill calls={len(reads)}')
assert not reads, 'Core-1 stale DREQ dispatched a new transfer after DSP stopped'
print('PASS: stopped block cannot be rearmed by consumer')
put(base+s['irq_await_play'],0)
put(base+s['dma_running'],1)
fw.call('sb16_poll',base)
fw.call('i8257_dma_run',dma)
assert reads, 'Core-0 polling failed to refill an active DSP'
print('PASS: active block still requests refill from core 0')
