#!/usr/bin/env python3
"""
WigiDash Single-Shot Test
One test per run. Unplug/replug device between runs.

Usage:
  ./wigidash-shot.py zeros              # 512 bytes of 0x00
  ./wigidash-shot.py byte 0x01          # first byte=0x01, rest zeros, 512 total
  ./wigidash-shot.py byte 0xFF          # first byte=0xFF, rest zeros
  ./wigidash-shot.py hex AABB0000...    # raw hex, padded to 512
  ./wigidash-shot.py scan-first         # try first bytes 0x00-0xFF, 256-byte packets (no processing trigger)
  ./wigidash-shot.py scan-full          # interactive: try first bytes one at a time, 512-byte packets
  ./wigidash-shot.py desc               # just dump descriptors, don't write anything
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


def cmd_desc():
    """Just dump descriptors."""
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("ERROR: WigiDash not found!")
        sys.exit(1)
    print(f"Found: {dev.manufacturer} {dev.product} serial={dev.serial_number}")
    for cfg in dev:
        for intf in cfg:
            print(f"  Interface {intf.bInterfaceNumber}: class={intf.bInterfaceClass}/{intf.bInterfaceSubClass}/{intf.bInterfaceProtocol}")
            for ep in intf:
                d = "IN" if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else "OUT"
                t = {0: "CTRL", 1: "ISO", 2: "BULK", 3: "INTR"}[usb.util.endpoint_type(ep.bmAttributes)]
                print(f"    EP 0x{ep.bEndpointAddress:02X} {d} {t} maxPkt={ep.wMaxPacketSize}")


def cmd_zeros():
    """Send 512 bytes of zeros."""
    dev = connect()
    data = b'\x00' * 512
    print(f"Sending 512 bytes of 0x00...")
    written, err = write_one(dev, data)
    print(f"Result: wrote={written} err={err}")


def cmd_byte(first_byte_str):
    """Send 512 bytes: first byte = arg, rest zeros."""
    first = int(first_byte_str, 0)
    dev = connect()
    data = bytes([first]) + b'\x00' * 511
    print(f"Sending 512 bytes: [0x{first:02X}] + 511 zeros...")
    written, err = write_one(dev, data)
    print(f"Result: wrote={written} err={err}")


def cmd_hex(hex_str):
    """Send raw hex data, padded to 512 bytes."""
    raw = bytes.fromhex(hex_str)
    dev = connect()
    if len(raw) < 512:
        data = raw + b'\x00' * (512 - len(raw))
    else:
        data = raw[:512]
    print(f"Sending 512 bytes: {data[:32].hex()}...")
    written, err = write_one(dev, data)
    print(f"Result: wrote={written} err={err}")


def cmd_scan_first():
    """Send 256-byte packets with different first bytes.
    256 bytes won't trigger processing (stays buffered), so we can
    scan through all first bytes safely on one connection."""
    dev = connect()
    print("Scanning first bytes 0x00-0xFF with 256-byte packets...")
    print("(256 bytes = no processing trigger, just buffering)\n")

    accepted = []
    rejected = []

    for first in range(0x100):
        data = bytes([first]) + b'\x00' * 255
        written, err = write_one(dev, data, timeout=2000)
        if err:
            rejected.append(first)
            if "no such device" in err.lower():
                print(f"  0x{first:02X}: DEVICE CRASHED!")
                break
            # If we get a timeout, device might be stuck
            if "timed out" in err.lower():
                print(f"  0x{first:02X}: TIMEOUT - device stuck, stopping scan")
                break
        else:
            accepted.append(first)
            sys.stdout.write(f"\r  Accepted: {len(accepted)} so far (last: 0x{first:02X})")
            sys.stdout.flush()
        time.sleep(0.02)

    print(f"\n\nAccepted {len(accepted)}/{len(accepted) + len(rejected)} first bytes")
    if rejected:
        print(f"First rejection at: 0x{rejected[0]:02X}")
    if accepted:
        print(f"Accepted range: 0x{accepted[0]:02X} - 0x{accepted[-1]:02X}")


def cmd_scan_full():
    """Interactive: try 512-byte packets one at a time.
    Prompts between each so you can replug if device freezes."""
    print("Interactive 512-byte scan. Replug device between tests if it freezes.\n")

    first = 0
    while first < 256:
        resp = input(f"Test first_byte=0x{first:02X}? [Enter=yes / s=skip / q=quit / number=jump to]: ").strip()
        if resp == 'q':
            break
        if resp == 's':
            first += 1
            continue
        if resp.startswith('0x') or resp.isdigit():
            first = int(resp, 0)
            continue

        dev = connect()
        data = bytes([first]) + b'\x00' * 511
        print(f"  Sending: [0x{first:02X}] + 511 zeros (512 total)...")
        written, err = write_one(dev, data)
        print(f"  Result: wrote={written} err={err}")

        if err:
            print("  Device may have frozen. Unplug/replug before next test.")
        else:
            print(f"  OK! Check screen for changes.")

        try:
            usb.util.release_interface(dev, 0)
        except:
            pass

        first += 1

    print("\nDone.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "desc":
        cmd_desc()
    elif cmd == "zeros":
        cmd_zeros()
    elif cmd == "byte" and len(sys.argv) >= 3:
        cmd_byte(sys.argv[2])
    elif cmd == "hex" and len(sys.argv) >= 3:
        cmd_hex(sys.argv[2])
    elif cmd == "scan-first":
        cmd_scan_first()
    elif cmd == "scan-full":
        cmd_scan_full()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
