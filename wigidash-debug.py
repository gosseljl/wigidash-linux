#!/usr/bin/env python3
"""
WigiDash Debug - Test the write setup control transfer in isolation.
Determines if the crash comes from the control transfer or the bulk write.
"""

import usb.core
import usb.util
import struct
import sys
import time

VENDOR_ID = 0x28DA
PRODUCT_ID = 0xEF01
EP_OUT = 0x01

RT_WRITE = 0x21
RT_READ  = 0xA1

FRAME_SIZE = 0x128000
TIMEOUT = 2000


def connect():
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("ERROR: WigiDash not found!")
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


def ctrl_write(dev, bRequest, wValue=0, data=None):
    if data is None:
        data = b''
    try:
        ret = dev.ctrl_transfer(RT_WRITE, bRequest, wValue, 0, data, TIMEOUT)
        return True, ret
    except usb.core.USBError as e:
        return False, str(e)


def ctrl_read(dev, bRequest, wValue=0, length=1):
    try:
        ret = dev.ctrl_transfer(RT_READ, bRequest, wValue, 0, length, TIMEOUT)
        return True, ret.tobytes()
    except usb.core.USBError as e:
        return False, str(e)


def is_alive(dev):
    """Check if device is still responding."""
    try:
        ret = dev.ctrl_transfer(RT_READ, 0x50, 0, 0, 1, TIMEOUT)
        return True, ret[0]
    except:
        return False, None


def test_setup_only():
    """Test: Send only the setup control transfer (0x61), no bulk write."""
    print("=" * 60)
    print("TEST 1: Setup control transfer ONLY (no bulk write)")
    print("=" * 60)

    dev = connect()

    # Verify device is alive
    alive, brightness = is_alive(dev)
    print(f"  Device alive before: {alive} (brightness={brightness})")

    # Send the exact setup control transfer
    setup_data = struct.pack('<II', 0, FRAME_SIZE)
    print(f"  Sending: ctrl_write(0x61, wValue=0, data={setup_data.hex()})")
    ok, ret = ctrl_write(dev, 0x61, 0, setup_data)
    print(f"  Result: ok={ok} ret={ret}")

    # Wait a bit
    time.sleep(0.5)

    # Check if device is still alive
    alive, brightness = is_alive(dev)
    print(f"  Device alive after: {alive} (brightness={brightness})")
    if not alive:
        print("  >>> DEVICE CRASHED FROM CONTROL TRANSFER ALONE!")
    else:
        print("  >>> Device survived the control transfer")


def test_setup_with_small_bulk():
    """Test: Setup + small bulk write (just 512 bytes)."""
    print("\n" + "=" * 60)
    print("TEST 2: Setup + small bulk write (512 bytes)")
    print("=" * 60)

    dev = connect()

    # GoToScreen(1) first
    ctrl_write(dev, 0x70, 0x20)
    time.sleep(0.3)

    setup_data = struct.pack('<II', 0, FRAME_SIZE)
    print(f"  Sending setup: ctrl_write(0x61, wValue=0, data={setup_data.hex()})")
    ok, ret = ctrl_write(dev, 0x61, 0, setup_data)
    print(f"  Setup result: ok={ok}")

    time.sleep(0.1)

    # Try small bulk write
    small_data = struct.pack('<H', 0xF800) * 256  # 512 bytes of red
    print(f"  Sending 512 bytes of pixel data...")
    try:
        written = dev.write(EP_OUT, small_data, timeout=2000)
        print(f"  Bulk write: {written} bytes written")
    except usb.core.USBError as e:
        print(f"  Bulk write FAILED: {e}")

    time.sleep(0.5)
    alive, brightness = is_alive(dev)
    print(f"  Device alive after: {alive}")


def test_nearby_cmds():
    """Test: Try nearby command values to see which ones don't crash."""
    print("\n" + "=" * 60)
    print("TEST 3: Probe nearby command bytes (0x60-0x65)")
    print("=" * 60)

    for cmd in [0x60, 0x61, 0x62, 0x63, 0x64, 0x65]:
        resp = input(f"\n  Test cmd=0x{cmd:02X}? [Enter=yes / s=skip / q=quit]: ").strip().lower()
        if resp == 'q':
            break
        if resp == 's':
            continue

        dev = connect()
        alive, _ = is_alive(dev)
        if not alive:
            print("  Device not responding, replug needed")
            continue

        # Send control write with this command, no data
        print(f"  Sending: ctrl_write(0x{cmd:02X}, wValue=0, no data)")
        ok, ret = ctrl_write(dev, cmd, 0)
        print(f"  Result: ok={ok} ret={ret}")

        time.sleep(0.5)
        alive, brightness = is_alive(dev)
        print(f"  Device alive: {alive} (brightness={brightness})")
        if not alive:
            print(f"  >>> cmd 0x{cmd:02X} CRASHED the device!")


def test_setup_data_variants():
    """Test: Try different setup data formats with 0x61."""
    print("\n" + "=" * 60)
    print("TEST 4: Different setup data formats with cmd 0x61")
    print("=" * 60)

    variants = [
        ("no data", b''),
        ("1 byte: 0x00", bytes([0])),
        ("4 bytes: size only", struct.pack('<I', FRAME_SIZE)),
        ("8 bytes: [0, size] (current)", struct.pack('<II', 0, FRAME_SIZE)),
        ("8 bytes: [size, 0]", struct.pack('<II', FRAME_SIZE, 0)),
        ("8 bytes: all zeros", b'\x00' * 8),
        ("8 bytes: [0, 512]", struct.pack('<II', 0, 512)),
    ]

    for label, data in variants:
        resp = input(f"\n  Test '{label}'? [Enter=yes / s=skip / q=quit]: ").strip().lower()
        if resp == 'q':
            break
        if resp == 's':
            continue

        dev = connect()
        alive, _ = is_alive(dev)
        if not alive:
            print("  Device not responding, replug needed")
            continue

        print(f"  Sending: ctrl_write(0x61, wValue=0, data={data.hex() if data else '(empty)'})")
        ok, ret = ctrl_write(dev, 0x61, 0, data)
        print(f"  Result: ok={ok} ret={ret}")

        time.sleep(0.5)
        alive, brightness = is_alive(dev)
        print(f"  Device alive: {alive} (brightness={brightness})")
        if not alive:
            print(f"  >>> CRASHED with data: {label}")
        else:
            print(f"  >>> SURVIVED with data: {label}")


def test_no_set_config():
    """Test: Skip set_configuration(), just claim_interface like the binary."""
    print("\n" + "=" * 60)
    print("TEST 5: Skip set_configuration (match binary)")
    print("=" * 60)

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("  ERROR: WigiDash not found!")
        return

    print(f"  Found WigiDash: Bus {dev.bus} Device {dev.address}")
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
    # Skip set_configuration!
    try:
        usb.util.claim_interface(dev, 0)
    except Exception as e:
        print(f"  Claim failed: {e}")
        return

    alive, brightness = is_alive(dev)
    print(f"  Alive: {alive} (brightness={brightness})")

    # GoToScreen(1)
    ctrl_write(dev, 0x70, 0x20)
    time.sleep(0.3)

    # Setup
    setup_data = struct.pack('<II', 0, FRAME_SIZE)
    print(f"  Sending setup: ctrl_write(0x61, wValue=0, data={setup_data.hex()})")
    ok, ret = ctrl_write(dev, 0x61, 0, setup_data)
    print(f"  Setup: ok={ok}")

    time.sleep(0.1)

    # Bulk write
    red = struct.pack('<H', 0xF800) * (FRAME_SIZE // 2)
    print(f"  Sending {len(red)} bytes...")
    try:
        written = dev.write(EP_OUT, red, timeout=5000)
        print(f"  Bulk write: {written} bytes!")
    except usb.core.USBError as e:
        print(f"  Bulk write FAILED: {e}")

    time.sleep(0.5)
    alive, _ = is_alive(dev)
    print(f"  Alive after: {alive}")


def test_bulk_only():
    """Test: Can we write to the bulk endpoint at ALL? No setup, just raw bulk."""
    print("\n" + "=" * 60)
    print("TEST 6: Raw bulk write (no setup control transfer)")
    print("=" * 60)

    dev = connect()
    alive, brightness = is_alive(dev)
    print(f"  Device alive: {alive} (brightness={brightness})")

    # Try writing 512 bytes of zeros to EP OUT - no setup at all
    print("  Sending 512 bytes of zeros to EP 0x01 (no setup)...")
    try:
        written = dev.write(EP_OUT, b'\x00' * 512, timeout=2000)
        print(f"  Bulk write: {written} bytes written")
    except usb.core.USBError as e:
        print(f"  Bulk write FAILED: {e}")

    time.sleep(0.5)
    alive, brightness = is_alive(dev)
    print(f"  Device alive after: {alive} (brightness={brightness})")
    if not alive:
        print("  >>> Device crashes on raw bulk writes!")
    else:
        print("  >>> Device survived raw bulk write")


def test_bulk_after_setup_progressive():
    """Test: Setup + various bulk sizes around the 512-byte boundary."""
    print("\n" + "=" * 60)
    print("TEST 7: Bulk sizes around max-packet-size (512) boundary")
    print("=" * 60)
    print("  USB max packet size = 512. Short packet (<512) = transfer complete.")
    print("  Full packet (=512) = device expects more data.\n")

    # Test sizes around the boundary
    sizes = [
        (64,   "short packet (64 < 512)"),
        (256,  "short packet (256 < 512)"),
        (511,  "short packet (511 < 512)"),
        (512,  "EXACT max packet size"),
        (513,  "512+1 = full pkt + 1-byte short pkt"),
        (1024, "2 full packets"),
        (1025, "2 full packets + 1-byte short pkt"),
    ]

    for size, desc in sizes:
        resp = input(f"  Test {size} bytes ({desc})? [Enter/s/q]: ").strip().lower()
        if resp == 'q':
            break
        if resp == 's':
            continue

        dev = connect()
        alive, _ = is_alive(dev)
        if not alive:
            print("  Device not responding, replug needed")
            continue

        # Setup with size matching actual data
        setup_data = struct.pack('<II', 0, size)
        ok, ret = ctrl_write(dev, 0x61, 0, setup_data)
        print(f"  Setup(size={size}): ok={ok}")
        time.sleep(0.05)

        # Bulk write - pad with red pixels
        data = (struct.pack('<H', 0xF800) * ((size // 2) + 1))[:size]
        try:
            written = dev.write(EP_OUT, data, timeout=2000)
            print(f"  Bulk: wrote {written} bytes")
        except usb.core.USBError as e:
            print(f"  Bulk: FAILED {e}")

        time.sleep(0.5)
        alive, brightness = is_alive(dev)
        status = "SURVIVED" if alive else "CRASHED"
        print(f"  >>> {status} (alive={alive}, brightness={brightness})\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: ./wigidash-debug.py [1|2|3|4|5|all]")
        print("  1: Setup control transfer only (no bulk)")
        print("  2: Setup + small bulk write (512 bytes)")
        print("  3: Probe nearby command bytes")
        print("  4: Different setup data formats")
        print("  5: Skip set_configuration")
        print("  all: Run tests 1-2 in sequence")
        sys.exit(1)

    test = sys.argv[1]

    if test == '1':
        test_setup_only()
    elif test == '2':
        test_setup_with_small_bulk()
    elif test == '3':
        test_nearby_cmds()
    elif test == '4':
        test_setup_data_variants()
    elif test == '5':
        test_no_set_config()
    elif test == '6':
        test_bulk_only()
    elif test == '7':
        test_bulk_after_setup_progressive()
    elif test == 'all':
        test_setup_only()
        input("\nReplug and press Enter for test 2...")
        test_setup_with_small_bulk()
    else:
        print(f"Unknown test: {test}")


if __name__ == "__main__":
    main()
