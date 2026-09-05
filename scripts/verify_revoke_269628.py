#!/usr/bin/env python3
"""Emulate the configured ARM64 guard and WeChat's sender predicate.

Requires unicorn (pip install unicorn). Reads an original 269628 wechat.dylib;
never modifies the app or sends messages.
"""
import argparse
import json
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1,
    UC_ARM64_REG_X2, UC_ARM64_REG_X8, UC_ARM64_REG_X19,
    UC_ARM64_REG_X21, UC_ARM64_REG_X22, UC_ARM64_REG_X30,
)

ROOT = Path(__file__).resolve().parents[1]
HOOK, CAVE, PREDICATE = 0x32420A0, 0x5B23180, 0x494D540
OWN, OTHER = 0x32420A4, 0x3242B64
ACCOUNT, MEMCMP, GET_NAME = 0x4317C9C, 0x6D97364, 0x10000000
STACK, HEAP = 0x20000000, 0x30000000


def arm64_slice(data):
    if data[:4] == bytes.fromhex('cafebabe'):
        for n in range(struct.unpack_from('>I', data, 4)[0]):
            cpu, _, offset, size, _ = struct.unpack_from('>IIIII', data, 8 + n * 20)
            if cpu == 0x100000C:
                return data[offset:offset + size]
        raise AssertionError('No ARM64 slice')
    assert data[:8] == bytes.fromhex('cffaedfe0c000001'), 'Expected ARM64 Mach-O'
    return data


def verify(binary):
    data = arm64_slice(binary.read_bytes())
    config = next(c for c in json.loads((ROOT / 'config.json').read_text())
                  if c['version'] == '269628')
    entries = config['targets'][0]['entries']
    patched = bytearray(data)
    for entry in entries:
        address = int(entry['addr'], 16)
        expected, patch = bytes.fromhex(entry['expected']), bytes.fromhex(entry['asm'])
        assert len(expected) == len(patch)
        assert data[address:address + len(expected)] == expected, hex(address)
        patched[address:address + len(patch)] = patch

    for me in ('my_account', 'wxid_' + 'a' * 32):
        for sender in (me, 'another_user', 'wxid_' + 'b' * 32, ''):
            for conversation in ('friend', '123456@chatroom'):
                uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
                pages = {a & ~0xFFF for a in (HOOK, CAVE, PREDICATE, OWN, OTHER, ACCOUNT, MEMCMP, GET_NAME)}
                for page in pages:
                    uc.mem_map(page, 0x1000)
                    if page < len(patched):
                        uc.mem_write(page, bytes(patched[page:page + 0x1000]))
                uc.mem_map(STACK, 0x4000)
                uc.mem_map(HEAP, 0x4000)
                sp = STACK + 0x2000
                cursor = HEAP + 0x1000

                def write64(address, value):
                    uc.mem_write(address, struct.pack('<Q', value))

                def string(address, value):
                    nonlocal cursor
                    raw = value.encode()
                    if len(raw) < 23:
                        uc.mem_write(address, raw + bytes(23 - len(raw)) + bytes([len(raw)]))
                    else:
                        uc.mem_write(cursor, raw + b'\0')
                        uc.mem_write(address, struct.pack('<QQQ', cursor, len(raw), (1 << 63) | (len(raw) + 1)))
                        cursor += 0x100

                # Native Message at sp+0x38: type, sender, receiver.
                uc.mem_write(sp + 0x44, struct.pack('<I', 1))
                string(sp + 0x50, sender)
                string(sp + 0x68, conversation)
                string(HEAP + 0x100, me)
                write64(HEAP, HEAP + 0x200)
                write64(HEAP + 0x228, GET_NAME)
                write64(sp + 0x720, 987654321)
                write64(sp + 0x2F0, HEAP + 0x400)
                uc.mem_write(sp + 0x14C, struct.pack('<I', 12345))
                uc.reg_write(UC_ARM64_REG_SP, sp)
                uc.reg_write(UC_ARM64_REG_X19, 0x1234)
                uc.reg_write(UC_ARM64_REG_X21, 0x5678)
                uc.reg_write(UC_ARM64_REG_X22, 0xABCD)
                reached = []

                def on_code(machine, address, size, _):
                    if address in (OWN, OTHER):
                        reached.append(address)
                        machine.emu_stop()
                    elif address in (ACCOUNT, GET_NAME, MEMCMP):
                        if address == ACCOUNT:
                            result = HEAP
                        elif address == GET_NAME:
                            result = HEAP + 0x100
                        else:
                            a, b, count = [machine.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2)]
                            result = int(machine.mem_read(a, count) != machine.mem_read(b, count))
                        machine.reg_write(UC_ARM64_REG_X0, result)
                        machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_X30))

                uc.hook_add(UC_HOOK_CODE, on_code)
                uc.emu_start(HOOK, 0, count=1000)
                own = sender == me
                assert reached == [OWN if own else OTHER], (me, sender, reached)
                assert struct.unpack('<Q', uc.mem_read(sp + 0x720, 8))[0] == (987654321 if own else 0)
                assert uc.reg_read(UC_ARM64_REG_SP) == sp
                assert uc.reg_read(UC_ARM64_REG_X19) == 0x1234
                assert uc.reg_read(UC_ARM64_REG_X21) == 0x5678
                assert uc.reg_read(UC_ARM64_REG_X22) == (0xABCD if own else HEAP + 0x400)
                if own:
                    assert uc.reg_read(UC_ARM64_REG_X8) == 12345
    print('PASS: original bytes; 16 sender/conversation cases; native predicate; continuations and registers')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    verify(parser.parse_args().binary)
