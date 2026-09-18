#!/usr/bin/env python3
"""Verify the 269629 updater patch against Objective-C metadata.

The script reads an original WeChat universal dylib, resolves the ARM64
implementations for the updater selectors, and checks that config.json points
to those implementations with the correct expected and replacement bytes.
It never modifies the input binary.
"""

import argparse
import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CPU_TYPE_ARM64 = 0x0100000C
MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
POINTER_PAYLOAD_MASK = 0x0000FFFFFFFFFFFF
PATCH = bytes.fromhex("00008052C0035FD6")

SELECTORS = {
    "startUpdater": "startUpdater",
    "startBackgroundUpdatesCheck": "startBackgroundUpdatesCheck:",
    "checkForUpdates": "checkForUpdates:",
    "enableAutoUpdate": "enableAutoUpdate:",
    "automaticallyDownloadsUpdates": "automaticallyDownloadsUpdates",
    "canCheckForUpdate": "canCheckForUpdate",
}


def arm64_slice(data):
    if data[:4] == bytes.fromhex("cafebabe"):
        count = struct.unpack_from(">I", data, 4)[0]
        for index in range(count):
            cpu, _, offset, size, _ = struct.unpack_from(
                ">IIIII", data, 8 + index * 20
            )
            if cpu == CPU_TYPE_ARM64:
                return data[offset : offset + size]
        raise AssertionError("No ARM64 slice")

    assert struct.unpack_from("<I", data, 0)[0] == MH_MAGIC_64
    assert struct.unpack_from("<I", data, 4)[0] == CPU_TYPE_ARM64
    return data


def sections(data):
    assert struct.unpack_from("<I", data, 0)[0] == MH_MAGIC_64
    result = {}
    command_offset = 32
    command_count = struct.unpack_from("<I", data, 16)[0]

    for _ in range(command_count):
        command, command_size = struct.unpack_from("<II", data, command_offset)
        if command == LC_SEGMENT_64:
            section_count = struct.unpack_from("<I", data, command_offset + 64)[0]
            section_offset = command_offset + 72
            for _ in range(section_count):
                section = data[section_offset : section_offset + 16].rstrip(b"\0").decode()
                segment = data[section_offset + 16 : section_offset + 32].rstrip(b"\0").decode()
                address, size, file_offset = struct.unpack_from(
                    "<QQI", data, section_offset + 32
                )
                result[(segment, section)] = (address, size, file_offset)
                section_offset += 80
        command_offset += command_size

    return result


def read_c_string(data, section_map, address):
    address &= POINTER_PAYLOAD_MASK
    for section_address, section_size, file_offset in section_map.values():
        if section_address <= address < section_address + section_size:
            start = file_offset + address - section_address
            end = data.index(0, start)
            return data[start:end].decode("utf-8", "replace")
    raise AssertionError(f"String address is outside known sections: {address:#x}")


def resolve_methods(data, section_map, selectors):
    sel_address, sel_size, sel_offset = section_map[("__DATA", "__objc_selrefs")]
    method_address, method_size, method_offset = section_map[
        ("__TEXT", "__objc_methlist")
    ]
    references = {selector: [] for selector in selectors}

    for offset in range(sel_offset, sel_offset + sel_size, 8):
        pointer = struct.unpack_from("<Q", data, offset)[0]
        selector = read_c_string(data, section_map, pointer)
        if selector in references:
            references[selector].append(sel_address + offset - sel_offset)

    method_data = data[method_offset : method_offset + method_size]
    implementations = {}
    for selector, selector_refs in references.items():
        matches = []
        for offset in range(0, len(method_data) - 12, 4):
            name_field = method_address + offset
            name_delta = struct.unpack_from("<i", method_data, offset)[0]
            if name_field + name_delta not in selector_refs:
                continue

            imp_field = name_field + 8
            imp_delta = struct.unpack_from("<i", method_data, offset + 8)[0]
            implementation = imp_field + imp_delta
            if 0 <= implementation < len(data):
                matches.append(implementation)

        assert len(matches) == 1, (selector, [hex(value) for value in matches])
        implementations[selector] = matches[0]

    return implementations


def verify(binary):
    data = arm64_slice(binary.read_bytes())
    section_map = sections(data)
    implementations = resolve_methods(data, section_map, set(SELECTORS.values()))
    configs = json.loads((ROOT / "config.json").read_text())
    config = next(item for item in configs if item["version"] == "269629")
    targets = {target["identifier"]: target for target in config["targets"]}

    assert set(targets) == set(SELECTORS)
    patched = bytearray(data)
    for identifier, selector in SELECTORS.items():
        target = targets[identifier]
        assert target["binary"] == "Contents/Resources/wechat.dylib"
        assert len(target["entries"]) == 1
        entry = target["entries"][0]
        assert entry["arch"] == "arm64"
        address = int(entry["addr"], 16)
        expected = bytes.fromhex(entry["expected"])
        replacement = bytes.fromhex(entry["asm"])
        assert address == implementations[selector], (
            identifier,
            hex(address),
            hex(implementations[selector]),
        )
        assert replacement == PATCH
        assert len(expected) == len(replacement)
        assert data[address : address + len(expected)] == expected, identifier
        patched[address : address + len(replacement)] = replacement
        assert patched[address : address + len(replacement)] == PATCH

    print(
        "PASS: 6 updater selectors resolved from Objective-C metadata; "
        "addresses, original bytes, target binary, and ARM64 return-false patch verified"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    verify(parser.parse_args().binary)
