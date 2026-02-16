#!/usr/bin/env python3
"""
WigiDash Command Payload Probe
Test specific command payloads. One test per run.

Usage:
  ./wigidash-cmd.py boundary              # find valid command range (0x05-0x0F, 0xF0-0xFE)
  ./wigidash-cmd.py probe CMD             # probe payload format for command CMD
  ./wigidash-cmd.py raw CMD OFFSET VAL    # send cmd with 16-bit LE value at byte offset
  ./wigidash-cmd.py fill CMD FILLBYTE     # send cmd with rest filled with FILLBYTE
  ./wigidash-cmd.py region CMD X Y W H    # send cmd with x,y,w,h header + red pixels
"""

import usb.core
import usb.util
import struct
import sys
import time

VENDOR_ID = 0x28DA
PRODUCT_ID = 0xEF01
EP_OUT = 0x01


def connect():
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("ERROR: WigiDash not found! Unplug/replug and try again.")
        sys.exit(1)
    print(f"Found WigiDash: Bus {dev.bus} Device {dev.address}")
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
    try:
        dev.set_configuration()
    except:
        pass
    try:
        usb.util.claim_interface(dev, 0)
    except:
        pass
    return dev


def write_one(dev, data, timeout=5000):
    try:
        written = dev.write(EP_OUT, data, timeout=timeout)
        return written, None
    except usb.core.USBError as e:
        return 0, str(e)


def cmd_boundary():
    """Find the valid command byte ranges."""
    dev = connect()
    print("Testing command bytes with 256-byte packets (no processing trigger).")
    print("Then testing with 512-byte packets for the interesting ones.\n")

    # First pass: 256-byte packets to see which ones are accepted without triggering
    # Actually we learned all 256-byte packets are buffered, so this won't help.
    # We need 512-byte packets, which means one at a time with replug.

    print("This test requires replug between crashes.")
    print("Testing 0x05 through 0x0F, then 0xF0 through 0xFE.\n")

    test_bytes = list(range(0x05, 0x10)) + list(range(0xF0, 0xFF))

    for b in test_bytes:
        resp = input(f"  Test 0x{b:02X}? [Enter=yes / s=skip / q=quit]: ").strip().lower()
        if resp == 'q':
            break
        if resp == 's':
            continue

        dev = connect()
        data = bytes([b]) + b'\x00' * 511
        written, err = write_one(dev, data)
        if err:
            print(f"    0x{b:02X}: err={err}")
            if "timed out" in err.lower():
                print(f"    0x{b:02X}: TIMEOUT (device may be frozen, replug)")
            elif "no such device" in err.lower():
                print(f"    0x{b:02X}: CRASH (replug needed)")
        else:
            print(f"    0x{b:02X}: wrote {written} - OK, no crash")
            # Check for screen changes
            input("    Any screen change? [Enter to continue]: ")
            try:
                usb.util.release_interface(dev, 0)
            except:
                pass


def cmd_probe(cmd_byte):
    """Probe payload format for a specific command.
    Try various structures to find what doesn't crash."""
    cmd = int(cmd_byte, 0)

    print(f"Probing payload format for command 0x{cmd:02X}")
    print("Each test is a 512-byte packet. Replug between crashes.\n")

    tests = [
        # (label, payload_bytes_after_cmd)
        # Try non-zero values at different offsets to see what prevents crash

        # Maybe it needs a valid length field
        ("len16LE=1 at offset 1",
         struct.pack('<H', 1) + b'\x00' * 509),
        ("len16LE=100 at offset 1",
         struct.pack('<H', 100) + b'\x00' * 509),
        ("len16LE=511 at offset 1",
         struct.pack('<H', 511) + b'\x00' * 509),
        ("len32LE=1 at offset 1",
         struct.pack('<I', 1) + b'\x00' * 507),
        ("len32LE=100 at offset 1",
         struct.pack('<I', 100) + b'\x00' * 507),
        ("len32LE=511 at offset 1",
         struct.pack('<I', 511) + b'\x00' * 507),

        # Maybe it needs valid dimensions (w, h)
        ("w=1 h=1 at offset 1 (16-bit LE)",
         struct.pack('<HH', 1, 1) + b'\x00' * 507),
        ("w=1024 h=600 at offset 1 (16-bit LE)",
         struct.pack('<HH', 1024, 600) + b'\x00' * 507),
        ("w=10 h=10 at offset 1 (16-bit LE)",
         struct.pack('<HH', 10, 10) + b'\x00' * 507),

        # Maybe x, y, w, h
        ("x=0 y=0 w=1 h=1 at offset 1 (16-bit LE)",
         struct.pack('<HHHH', 0, 0, 1, 1) + b'\x00' * 503),
        ("x=0 y=0 w=1024 h=600 at offset 1 (16-bit LE)",
         struct.pack('<HHHH', 0, 0, 1024, 600) + b'\x00' * 503),
        ("x=0 y=0 w=10 h=10 at offset 1 (16-bit LE)",
         struct.pack('<HHHH', 0, 0, 10, 10) + b'\x00' * 503),

        # Maybe sub-command + dimensions
        ("subcmd=0x01 w=1024 h=600 at offset 1",
         struct.pack('<BHHH', 0x01, 1024, 600, 0) + b'\x00' * 504),
        ("subcmd=0x01 w=10 h=10 at offset 1",
         struct.pack('<BHHH', 0x01, 10, 10, 0) + b'\x00' * 504),

        # All 0xFF payload (maybe zeros are the problem, not the structure)
        ("all 0xFF payload",
         b'\xFF' * 511),

        # All 0x01 payload
        ("all 0x01 payload",
         b'\x01' * 511),

        # First 8 bytes = 0x01, rest zeros
        ("first 8 bytes = 0x01",
         b'\x01' * 8 + b'\x00' * 503),

        # Maybe it's a sub-command byte then a 32-bit length
        ("byte=0x01 + len32=503",
         struct.pack('<BI', 0x01, 503) + b'\x00' * 506),

        # What if offset 1 is a sub-command and offset 2-3 is length?
        ("sub=0x01 len16=508",
         struct.pack('<BH', 0x01, 508) + b'\x00' * 508),

        # JPEG-like: what if it expects a total transfer length?
        # cmd + 4-byte total length, then the device expects that many bytes
        ("cmd + totallen32=512",
         struct.pack('<I', 512) + b'\x00' * 507),
        ("cmd + totallen32=1228800 (full frame RGB565)",
         struct.pack('<I', 1228800) + b'\x00' * 507),
    ]

    for label, payload in tests:
        resp = input(f"  Test '{label}'? [Enter=yes / s=skip / q=quit]: ").strip().lower()
        if resp == 'q':
            break
        if resp == 's':
            continue

        dev = connect()
        data = bytes([cmd]) + payload[:511]
        assert len(data) == 512, f"Bad packet size: {len(data)}"

        print(f"    Sending: {data[:16].hex()}...")
        written, err = write_one(dev, data)
        if err:
            print(f"    RESULT: err={err}")
            if "timed out" in err.lower() or "no such device" in err.lower():
                print(f"    CRASH/FREEZE - replug needed")
        else:
            print(f"    RESULT: wrote {written} - NO CRASH!")
            input("    >>> Any screen change? Describe and press Enter: ")
            try:
                usb.util.release_interface(dev, 0)
            except:
                pass


def cmd_raw(cmd_byte, offset_str, val_str):
    """Send a command with a 16-bit LE value at a specific byte offset."""
    cmd = int(cmd_byte, 0)
    offset = int(offset_str, 0)
    val = int(val_str, 0)

    dev = connect()
    data = bytearray(512)
    data[0] = cmd
    struct.pack_into('<H', data, offset, val)
    print(f"Sending cmd=0x{cmd:02X} with uint16 {val} at offset {offset}")
    print(f"  Packet: {bytes(data[:16]).hex()}...")
    written, err = write_one(dev, bytes(data))
    print(f"  Result: wrote={written} err={err}")


def cmd_fill(cmd_byte, fill_str):
    """Send command with payload filled with a specific byte."""
    cmd = int(cmd_byte, 0)
    fill = int(fill_str, 0)

    dev = connect()
    data = bytes([cmd]) + bytes([fill]) * 511
    print(f"Sending cmd=0x{cmd:02X} payload=0x{fill:02X}*511")
    written, err = write_one(dev, data)
    print(f"  Result: wrote={written} err={err}")


def cmd_region(cmd_byte, x_str, y_str, w_str, h_str):
    """Send command with x,y,w,h header and red pixel data."""
    cmd = int(cmd_byte, 0)
    x, y, w, h = int(x_str, 0), int(y_str, 0), int(w_str, 0), int(h_str, 0)

    dev = connect()
    data = bytearray(512)
    data[0] = cmd
    # Try packing x, y, w, h as 16-bit LE starting at offset 1
    struct.pack_into('<HHHH', data, 1, x, y, w, h)
    # Fill remaining with red RGB565 (0xF800)
    for i in range(9, 512, 2):
        if i + 1 < 512:
            struct.pack_into('<H', data, i, 0xF800)

    print(f"Sending cmd=0x{cmd:02X} x={x} y={y} w={w} h={h} + red pixels")
    print(f"  Packet: {bytes(data[:16]).hex()}...")
    written, err = write_one(dev, bytes(data))
    print(f"  Result: wrote={written} err={err}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "boundary":
        cmd_boundary()
    elif cmd == "probe" and len(sys.argv) >= 3:
        cmd_probe(sys.argv[2])
    elif cmd == "raw" and len(sys.argv) >= 5:
        cmd_raw(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "fill" and len(sys.argv) >= 4:
        cmd_fill(sys.argv[2], sys.argv[3])
    elif cmd == "region" and len(sys.argv) >= 7:
        cmd_region(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
