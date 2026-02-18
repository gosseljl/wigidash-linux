#!/usr/bin/env python3
"""
WigiDash USB Probe Tool v4 - Single-Shot with Reset
Each test sends ONE complete write on a fresh device, then resets.
This avoids poisoning state between tests.
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
    """Find and set up the device."""
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        return None
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


def reset_device(dev):
    """USB reset and re-enumerate."""
    try:
        usb.util.release_interface(dev, 0)
    except:
        pass
    try:
        dev.reset()
    except:
        pass
    time.sleep(2)  # Wait for re-enumeration


def single_write(dev, data, timeout=5000):
    """Single bulk write, return (bytes_written, error)."""
    try:
        written = dev.write(EP_OUT, data, timeout=timeout)
        return written, None
    except usb.core.USBError as e:
        return 0, str(e)


def test_single_shot(label, data, timeout=5000):
    """Connect fresh, send one write, report, reset."""
    dev = connect()
    if dev is None:
        print(f"  {label}: DEVICE NOT FOUND (wait and retry)")
        return False

    written, err = single_write(dev, data, timeout=timeout)
    if err:
        print(f"  {label}: err: {err}")
    else:
        print(f"  {label}: wrote {written}/{len(data)} bytes OK")

    reset_device(dev)
    time.sleep(1)
    return True


def phase1_find_accepted_sizes():
    """Figure out what transfer sizes the device accepts starting with 0x00."""
    print("=" * 60)
    print("PHASE 1: What sizes does the device accept?")
    print("=" * 60)
    print("Each test: fresh connect -> single write -> reset\n")

    # The device accepted a 1-byte 0x00. Let's see what sizes work.
    # Hypothesis: 0x00 starts a command that expects N more bytes.

    # Test: does 0x00 still work on fresh device?
    print("--- Verify 0x00 single byte ---")
    test_single_shot("1 byte: 0x00", b'\x00')

    # Test various sizes of all-zeros
    print("\n--- All-zero payloads of various sizes ---")
    for size in [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]:
        data = b'\x00' * size
        test_single_shot(f"{size} bytes of 0x00", data)

    input("\nAny screen changes during that? Press Enter...")

    # What about a full frame?
    print("\n--- Full frame sizes (all zeros) ---")
    for size in [1024 * 600 * 2, 1024 * 600 * 3, 1024 * 600 * 4]:
        data = b'\x00' * size
        test_single_shot(f"{size} bytes ({size // 1024}K) of 0x00", data, timeout=10000)

    input("\nAny screen changes? Press Enter...")


def phase2_first_byte_matters():
    """Test if the first byte determines what happens."""
    print("\n" + "=" * 60)
    print("PHASE 2: Does the first byte matter?")
    print("=" * 60)
    print("Try different first bytes with 512-byte payloads.\n")

    # 512 bytes is a natural USB packet size
    for first_byte in range(0x00, 0x20):
        data = bytes([first_byte]) + b'\x00' * 511
        test_single_shot(f"first=0x{first_byte:02x} + 511 zeros (512 total)", data)

    input("\nAny screen changes? Press Enter...")

    # Try some higher values
    print("\n--- Higher first byte values ---")
    for first_byte in [0x20, 0x40, 0x55, 0x80, 0xAA, 0xC0, 0xFE, 0xFF]:
        data = bytes([first_byte]) + b'\x00' * 511
        test_single_shot(f"first=0x{first_byte:02x} + 511 zeros", data)

    input("\nAny screen changes? Press Enter...")


def phase3_multi_write_after_0x00():
    """Since 0x00 is accepted, try following up immediately with more data."""
    print("\n" + "=" * 60)
    print("PHASE 3: Follow up 0x00 with immediate data")
    print("=" * 60)
    print("Send 0x00, then immediately try to send frame data.\n")

    # Maybe 0x00 is an "init" and then the device expects frame data
    # in subsequent transfers. Try with a very short gap.

    dev = connect()
    if dev is None:
        print("  DEVICE NOT FOUND")
        return

    # Send 0x00
    written, err = single_write(dev, b'\x00', timeout=5000)
    print(f"  Init 0x00: wrote={written} err={err}")

    if err is None:
        # Immediately try to send more data with short timeout
        # Maybe the device is ready right after accepting 0x00
        time.sleep(0.01)  # 10ms gap

        # Try another 0x00
        written2, err2 = single_write(dev, b'\x00', timeout=5000)
        print(f"  Second 0x00: wrote={written2} err={err2}")

        if err2 is None:
            # It accepted two! Try more
            for i in range(10):
                w, e = single_write(dev, b'\x00', timeout=2000)
                print(f"  0x00 #{i + 3}: wrote={w} err={e}")
                if e:
                    break
                time.sleep(0.01)

    reset_device(dev)
    time.sleep(1)

    input("\nPress Enter for next test...")

    # Try 0x00 then a big block
    dev = connect()
    if dev is None:
        print("  DEVICE NOT FOUND")
        return

    written, err = single_write(dev, b'\x00', timeout=5000)
    print(f"\n  Init 0x00: wrote={written} err={err}")

    if err is None:
        # Try sending a full RGB565 frame right after
        frame_size = 1024 * 600 * 2
        frame = b'\xF8\x00' * (1024 * 600)  # Red in RGB565 LE (0x00F8 -> 0xF800)
        print(f"  Sending {frame_size} byte frame...")
        w, e = single_write(dev, frame, timeout=10000)
        print(f"  Frame: wrote={w} err={e}")

    reset_device(dev)
    time.sleep(1)

    input("\nAny screen change? Press Enter...")


def phase4_longer_timeouts():
    """Maybe the device is just very slow. Try much longer timeouts."""
    print("\n" + "=" * 60)
    print("PHASE 4: Longer timeouts")
    print("=" * 60)
    print("Maybe the device needs more time. Trying 10s+ timeouts.\n")

    # Send 0x01 with a 10-second timeout on fresh device
    for first_byte in [0x01, 0x02, 0x10, 0xFF]:
        data = bytes([first_byte]) + b'\x00' * 511
        test_single_shot(f"first=0x{first_byte:02x} (10s timeout)", data, timeout=10000)


def phase5_raw_framebuffer():
    """Maybe the device just wants raw pixels with no command at all."""
    print("\n" + "=" * 60)
    print("PHASE 5: Raw framebuffer blast")
    print("=" * 60)
    print("Try sending exactly one frame of pixels, no headers.\n")

    # RGB565, 1024x600
    frame_size = 1024 * 600 * 2

    # Solid red
    red = struct.pack('<H', 0xF800) * (1024 * 600)
    test_single_shot(f"Raw RGB565 red frame ({frame_size} bytes)", red, timeout=15000)

    # Solid white
    white = b'\xFF\xFF' * (1024 * 600)
    test_single_shot(f"Raw RGB565 white frame ({frame_size} bytes)", white, timeout=15000)

    # Solid blue
    blue = struct.pack('<H', 0x001F) * (1024 * 600)
    test_single_shot(f"Raw RGB565 blue frame ({frame_size} bytes)", blue, timeout=15000)

    input("\nAny screen changes at all? Press Enter...")

    # Maybe RGB888?
    frame888 = 1024 * 600 * 3
    red888 = (b'\xFF\x00\x00') * (1024 * 600)
    test_single_shot(f"Raw RGB888 red frame ({frame888} bytes)", red888, timeout=15000)

    # Maybe BGRA8888?
    frame32 = 1024 * 600 * 4
    red_bgra = (b'\x00\x00\xFF\xFF') * (1024 * 600)
    test_single_shot(f"Raw BGRA8888 red frame ({frame32} bytes)", red_bgra, timeout=15000)

    input("\nAny screen changes? Press Enter...")


def main():
    print("WigiDash Single-Shot Probe v4")
    print("=" * 40)
    print("Each test: fresh connect -> one write -> USB reset")
    print("This keeps the device clean between tests.\n")

    # Verify device exists first
    dev = connect()
    if dev is None:
        print("ERROR: WigiDash not found!")
        sys.exit(1)
    print(f"Found WigiDash: Bus {dev.bus} Device {dev.address}")
    reset_device(dev)
    time.sleep(1)

    try:
        phase1_find_accepted_sizes()
        phase2_first_byte_matters()
        phase3_multi_write_after_0x00()
        phase4_longer_timeouts()
        phase5_raw_framebuffer()

        print("\n" + "=" * 60)
        print("ALL PHASES COMPLETE")
        print("=" * 60)
        print("Share all output and screen observations!")

    except KeyboardInterrupt:
        print("\n\nInterrupted.")


if __name__ == "__main__":
    main()
