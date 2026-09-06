"""A failed memory guard must exit BEFORE the offending guest instruction."""
import argparse
from elf_harness import Firmware


def run(elf):
    count = 0
    for op in (0x00, 0x02, 0x28, 0x2A, 0x30, 0x32):
        # Writes to a translated code page must use interpreter invalidation.
        addresses = [0xA0000, 0xBFFFF, Firmware.RAM_SIZE, 0xFFFFFFFF]
        if not (op & 2):
            addresses.append(Firmware.START)
        for address in addresses:
            machine = Firmware(elf)
            try:
                # First a harmless MOV completes, then the guard must side-exit.
                code = b"\x89\xc8" + bytes((op, 6)) * 6 + b"\x0f\x0b"
                regs = [0x123480FF, 0x56, 0x789ABCDE, 0, 0x8000, 0, address, 0]
                machine.prepare(code, regs)
                before = machine.snapshot()
                before["registers"] = tuple([regs[1]] + regs[1:])
                before["next_ip"] += 2
                protected = None
                if address < machine.RAM_SIZE:
                    protected = bytes(machine.uc.mem_read(machine.RAM + address, 1))
                block = machine.compile()
                if block is None:
                    raise AssertionError("Guard test did not compile")
                done = machine.call(block[0], machine.CPU, 1)
                after = machine.snapshot()
                if done != 1 or after != before:
                    raise AssertionError(f"opcode={op:02x}, address={address:08x}, done={done}: {after} != {before}")
                if protected is not None and protected != bytes(machine.uc.mem_read(machine.RAM + address, 1)):
                    raise AssertionError("Guard allowed a protected write")
                count += 1
            finally:
                machine.close()
    print(f"PASS: {count} memory guard side exits (MMIO, bounds, code writes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf")
    run(parser.parse_args().elf)
