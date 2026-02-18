#!/usr/bin/env python3
"""
WigiDash Multi-Packet Probe
Send command header + follow-up pixel data as fast as possible.

Usage:
  ./wigidash-multi.py stream CMD NPKTS       # send CMD header + NPKTS more 512-byte packets of red pixels
  ./wigidash-multi.py bigwrite CMD NBYTES     # send CMD + NBYTES as one big write() call
  ./wigidash-multi.py init-then-draw          # try init sequences then draw
  ./wigidash-multi.py try-all-cmds            # probe cmds 0x00-0x04 with non-zero payloads (interactive)
  ./wigidash-multi.py two-phase CMD1 CMD2     # send two 512-byte packets: CMD1 then CMD2
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


def make_header(cmd, **kwargs):
    """Build a 512-byte header packet with various formats."""
    data = bytearray(512)
    data[0] = cmd

    # Fill in kwargs at specified offsets or named fields
    fmt = kwargs.get('fmt', 'xywh16')

    if fmt == 'xywh16':
        # cmd(1) + x(2) + y(2) + w(2) + h(2) = 9 bytes header
        x = kwargs.get('x', 0)
        y = kwargs.get('y', 0)
        w = kwargs.get('w', 1024)
        h = kwargs.get('h', 600)
        struct.pack_into('<HHHH', data, 1, x, y, w, h)
    elif fmt == 'len_xywh16':
        # cmd(1) + len(4) + x(2) + y(2) + w(2) + h(2) = 13 bytes header
        x = kwargs.get('x', 0)
        y = kwargs.get('y', 0)
        w = kwargs.get('w', 1024)
        h = kwargs.get('h', 600)
        nbytes = kwargs.get('nbytes', w * h * 2)
        struct.pack_into('<IHHHH', data, 1, nbytes, x, y, w, h)
    elif fmt == 'raw':
        # Just the command byte, rest is whatever's passed
        pass

    return bytes(data)


def make_pixels(w, h, color_rgb565=0xF800):
    """Generate RGB565 pixel data."""
    pixel = struct.pack('<H', color_rgb565)
    return pixel * (w * h)


def cmd_stream(cmd_str, npkts_str):
    """Send a header packet then N more packets of red pixel data."""
    cmd = int(cmd_str, 0)
    npkts = int(npkts_str, 0)

    dev = connect()

    # Header with full-screen dimensions
    header = make_header(cmd, fmt='xywh16', x=0, y=0, w=1024, h=600)
    print(f"Sending cmd=0x{cmd:02X} header: {header[:16].hex()}")
    written, err = write_one(dev, header, timeout=5000)
    print(f"  Header: wrote={written} err={err}")
    if err:
        print("  Header failed. Device may need replug.")
        return

    # Immediately send pixel data packets
    red = struct.pack('<H', 0xF800) * 256  # 512 bytes = 256 red pixels
    for i in range(npkts):
        written, err = write_one(dev, red, timeout=2000)
        if err:
            print(f"  Pixel packet {i}: err={err}")
            if "no such device" in err.lower():
                print("  Device crashed!")
                return
            print("  Stopping.")
            break
        else:
            print(f"  Pixel packet {i}: wrote {written}")
        # No sleep - blast as fast as possible

    print("Done sending. Check screen.")


def cmd_bigwrite(cmd_str, nbytes_str):
    """Send command + N bytes as one single write() call.
    This lets the kernel/USB stack handle packetization."""
    cmd = int(cmd_str, 0)
    nbytes = int(nbytes_str, 0)

    dev = connect()

    # Build one big buffer: header + pixel data
    data = bytearray(nbytes)
    data[0] = cmd
    # x=0, y=0, w=1024, h=600 at offset 1
    struct.pack_into('<HHHH', data, 1, 0, 0, 1024, 600)
    # Fill rest with red pixels
    red = struct.pack('<H', 0xF800)
    for i in range(9, nbytes - 1, 2):
        data[i] = red[0]
        data[i + 1] = red[1]

    print(f"Sending {nbytes} bytes as single write: cmd=0x{cmd:02X} + xywh + red pixels")
    print(f"  First 16 bytes: {bytes(data[:16]).hex()}")

    start = time.time()
    written, err = write_one(dev, bytes(data), timeout=15000)
    elapsed = time.time() - start
    print(f"  Result: wrote={written}/{nbytes} in {elapsed:.1f}s err={err}")


def cmd_init_then_draw():
    """Try various initialization sequences before sending pixel data."""
    print("Testing init sequences before draw commands.")
    print("Each test: init packet(s) -> draw packet + pixels")
    print("Replug between crashes.\n")

    tests = [
        # (label, init_packets, draw_cmd, draw_data_bytes)
        ("reset then cmd 0x01 + frame",
         [b'\x00' * 512],  # reset first
         0x01,
         1024 * 600 * 2),

        ("reset then cmd 0x02 + frame",
         [b'\x00' * 512],
         0x02,
         1024 * 600 * 2),

        ("cmd 0x03 as init, then cmd 0x01 + frame",
         [bytes([0x03]) + b'\x00' * 511],
         0x01,
         1024 * 600 * 2),

        ("cmd 0x04 as init, then cmd 0x01 + frame",
         [bytes([0x04]) + b'\x00' * 511],
         0x01,
         1024 * 600 * 2),

        ("cmd 0x04 as init, then cmd 0x02 + frame",
         [bytes([0x04]) + b'\x00' * 511],
         0x02,
         1024 * 600 * 2),

        # Maybe 0x03 = set mode, 0x01 = write pixels?
        ("cmd 0x03 mode=1, then cmd 0x01 + frame",
         [bytes([0x03, 0x01]) + b'\x00' * 510],
         0x01,
         1024 * 600 * 2),
    ]

    for label, init_pkts, draw_cmd, draw_bytes in tests:
        resp = input(f"  Test '{label}'? [Enter=yes / s=skip / q=quit]: ").strip().lower()
        if resp == 'q':
            break
        if resp == 's':
            continue

        dev = connect()

        # Send init packets
        for i, pkt in enumerate(init_pkts):
            written, err = write_one(dev, pkt, timeout=5000)
            print(f"    Init {i}: wrote={written} err={err}")
            if err:
                print(f"    Init failed, skipping rest.")
                break
            time.sleep(1)  # Wait for reset to complete if applicable
        else:
            # Send draw header
            header = make_header(draw_cmd, fmt='xywh16', x=0, y=0, w=1024, h=600)
            # Append pixel data
            total = bytearray(header)
            red = struct.pack('<H', 0xF800)
            pixels = red * min(draw_bytes // 2, (512 * 100) // 2)  # Cap at ~50KB for testing
            full_data = bytes(total) + bytes(pixels)

            print(f"    Sending draw cmd=0x{draw_cmd:02X} + {len(pixels)} bytes pixels ({len(full_data)} total)")
            written, err = write_one(dev, full_data, timeout=15000)
            print(f"    Result: wrote={written}/{len(full_data)} err={err}")

        input("    Screen status? Press Enter: ")


def cmd_try_all():
    """Try commands 0x00-0x04 with specific non-zero payloads."""
    print("Testing each command with plausible payloads.")
    print("Replug between crashes.\n")

    # For each command, try payloads that might make sense
    for cmd in range(0x05):
        payloads = []

        if cmd == 0x00:
            # We know all-zeros = reset. Try with params.
            payloads = [
                ("0x00 + byte1=0x01 (init mode?)",
                 bytes([0x00, 0x01]) + b'\x00' * 510),
                ("0x00 + byte1=0x02",
                 bytes([0x00, 0x02]) + b'\x00' * 510),
                ("0x00 + byte1=0xFF",
                 bytes([0x00, 0xFF]) + b'\x00' * 510),
            ]
        else:
            # Try with plausible display params
            payloads = [
                (f"0x{cmd:02X} + w=1024 h=600 (LE16) + red pixels",
                 bytes([cmd]) + struct.pack('<HH', 1024, 600) + (struct.pack('<H', 0xF800) * 252) + b'\x00' * 3),
                (f"0x{cmd:02X} + x=0 y=0 w=1024 h=600 (LE16) + red pixels",
                 bytes([cmd]) + struct.pack('<HHHH', 0, 0, 1024, 600) + (struct.pack('<H', 0xF800) * 251) + b'\x00'),
                (f"0x{cmd:02X} + len32={1024*600*2} + x=0 y=0 w=1024 h=600",
                 bytes([cmd]) + struct.pack('<IHHHH', 1024*600*2, 0, 0, 1024, 600) + b'\x00' * (512 - 13)),
            ]

        for label, data in payloads:
            assert len(data) == 512, f"Bad size: {len(data)} for {label}"
            resp = input(f"  Test '{label}'? [Enter=yes / s=skip / q=quit]: ").strip().lower()
            if resp == 'q':
                return
            if resp == 's':
                continue

            dev = connect()
            print(f"    Sending: {data[:16].hex()}...")
            written, err = write_one(dev, data)
            print(f"    Result: wrote={written} err={err}")
            input("    Screen status? Press Enter: ")


def cmd_two_phase(cmd1_str, cmd2_str):
    """Send two sequential 512-byte packets with different commands."""
    cmd1 = int(cmd1_str, 0)
    cmd2 = int(cmd2_str, 0)

    dev = connect()

    pkt1 = bytes([cmd1]) + b'\x00' * 511
    pkt2 = bytearray(512)
    pkt2[0] = cmd2
    struct.pack_into('<HHHH', pkt2, 1, 0, 0, 1024, 600)
    # Fill rest with red
    red = struct.pack('<H', 0xF800)
    for i in range(9, 512, 2):
        pkt2[i] = red[0]
        pkt2[i + 1] = red[1]

    print(f"Packet 1: cmd=0x{cmd1:02X} + zeros")
    written1, err1 = write_one(dev, pkt1, timeout=5000)
    print(f"  Result: wrote={written1} err={err1}")

    if err1:
        print("  First packet failed. Done.")
        return

    time.sleep(0.5)  # Small gap

    print(f"Packet 2: cmd=0x{cmd2:02X} + xywh + red pixels")
    written2, err2 = write_one(dev, bytes(pkt2), timeout=5000)
    print(f"  Result: wrote={written2} err={err2}")

    print("Check screen.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "stream" and len(sys.argv) >= 4:
        cmd_stream(sys.argv[2], sys.argv[3])
    elif cmd == "bigwrite" and len(sys.argv) >= 4:
        cmd_bigwrite(sys.argv[2], sys.argv[3])
    elif cmd == "init-then-draw":
        cmd_init_then_draw()
    elif cmd == "try-all-cmds":
        cmd_try_all()
    elif cmd == "two-phase" and len(sys.argv) >= 4:
        cmd_two_phase(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
