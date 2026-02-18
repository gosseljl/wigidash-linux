#!/usr/bin/env python3
"""
WigiDash USB Probe Tool v3 - Clean Start
Skip control transfers entirely. Dump descriptors, find all endpoints,
try bidirectional communication on a fresh device.
"""

import usb.core
import usb.util
import struct
import sys
import time

VENDOR_ID = 0x28DA
PRODUCT_ID = 0xEF01

def dump_descriptors(dev):
    """Dump full USB descriptor tree."""
    print("=" * 60)
    print("DEVICE DESCRIPTORS")
    print("=" * 60)
    print(f"  idVendor:  0x{dev.idVendor:04X}")
    print(f"  idProduct: 0x{dev.idProduct:04X}")
    print(f"  bcdUSB:    {dev.bcdUSB:#06x}")
    print(f"  bcdDevice: {dev.bcdDevice:#06x}")
    print(f"  Class/Sub/Proto: {dev.bDeviceClass}/{dev.bDeviceSubClass}/{dev.bDeviceProtocol}")
    print(f"  Max Packet Size (EP0): {dev.bMaxPacketSize0}")
    print(f"  Num Configurations: {dev.bNumConfigurations}")

    try:
        print(f"  Manufacturer: {dev.manufacturer}")
    except:
        print(f"  Manufacturer: (unreadable)")
    try:
        print(f"  Product: {dev.product}")
    except:
        print(f"  Product: (unreadable)")
    try:
        print(f"  Serial: {dev.serial_number}")
    except:
        print(f"  Serial: (unreadable)")

    endpoints_out = []
    endpoints_in = []

    for cfg in dev:
        print(f"\n  Configuration {cfg.bConfigurationValue}:")
        print(f"    Num Interfaces: {cfg.bNumInterfaces}")
        print(f"    Max Power: {cfg.bMaxPower * 2} mA")

        for intf in cfg:
            print(f"\n    Interface {intf.bInterfaceNumber} Alt {intf.bAlternateSetting}:")
            print(f"      Class/Sub/Proto: {intf.bInterfaceClass}/{intf.bInterfaceSubClass}/{intf.bInterfaceProtocol}")
            print(f"      Num Endpoints: {intf.bNumEndpoints}")

            for ep in intf:
                direction = "IN" if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else "OUT"
                ep_type = {
                    usb.util.ENDPOINT_TYPE_BULK: "BULK",
                    usb.util.ENDPOINT_TYPE_INTR: "INTERRUPT",
                    usb.util.ENDPOINT_TYPE_ISO: "ISOCHRONOUS",
                    usb.util.ENDPOINT_TYPE_CTRL: "CONTROL",
                }.get(usb.util.endpoint_type(ep.bmAttributes), "UNKNOWN")

                print(f"      EP 0x{ep.bEndpointAddress:02X}: {direction} {ep_type}  maxPacket={ep.wMaxPacketSize}  interval={ep.bInterval}")

                if direction == "OUT":
                    endpoints_out.append(ep)
                else:
                    endpoints_in.append(ep)

    print(f"\n  Summary: {len(endpoints_out)} OUT endpoint(s), {len(endpoints_in)} IN endpoint(s)")
    return endpoints_out, endpoints_in


def try_read(dev, ep_in, size=512, timeout=1000):
    """Try reading from an IN endpoint."""
    try:
        data = dev.read(ep_in.bEndpointAddress, size, timeout=timeout)
        return data.tobytes(), None
    except usb.core.USBError as e:
        return None, str(e)


def try_write(dev, ep_out, data, timeout=2000):
    """Write to an OUT endpoint."""
    try:
        written = dev.write(ep_out.bEndpointAddress, data, timeout=timeout)
        return written, None
    except usb.core.USBError as e:
        return 0, str(e)


def phase1_check_inbound(dev, ep_in):
    """Check if the device sends anything unprompted."""
    print("\n" + "=" * 60)
    print("PHASE 1: Check for unsolicited IN data")
    print("=" * 60)
    print("Reading from IN endpoint to see if device sends anything...\n")

    for attempt in range(3):
        data, err = try_read(dev, ep_in, 512, timeout=2000)
        if data:
            print(f"  Read {len(data)} bytes: {data[:64].hex()}")
            if len(data) > 64:
                print(f"  ... ({len(data)} total bytes)")
            # Try to interpret as ASCII
            try:
                text = data.decode('ascii', errors='replace')
                print(f"  As ASCII: {text[:80]}")
            except:
                pass
        else:
            print(f"  Attempt {attempt + 1}: {err}")
        time.sleep(0.5)


def phase2_clean_bulk_writes(dev, ep_out, ep_in):
    """Try bulk writes on a clean device, checking for responses."""
    print("\n" + "=" * 60)
    print("PHASE 2: Clean bulk writes with response checking")
    print("=" * 60)

    has_in = ep_in is not None

    def write_and_check(label, data):
        """Write data, then check for a response if we have an IN endpoint."""
        written, err = try_write(dev, ep_out, data)
        if err:
            print(f"  {label}: WRITE err: {err}")
            if "no such device" in err.lower():
                return False
            # If write timed out but we have IN, try reading anyway
            if has_in and "timed out" in err.lower():
                resp, rerr = try_read(dev, ep_in, 512, timeout=500)
                if resp:
                    print(f"    Response after timeout: {resp[:64].hex()}")
            return True

        print(f"  {label}: wrote {written}")
        if has_in:
            time.sleep(0.05)
            resp, rerr = try_read(dev, ep_in, 512, timeout=1000)
            if resp:
                print(f"    Response: [{len(resp)} bytes] {resp[:64].hex()}")
                try:
                    print(f"    As ASCII: {resp.decode('ascii', errors='replace')[:80]}")
                except:
                    pass
            else:
                print(f"    No response ({rerr})")
        return True

    # Single bytes 0x00 through 0x0F
    print("\n--- Single bytes ---")
    for b in range(0x10):
        if not write_and_check(f"0x{b:02x}", bytes([b])):
            return False
        time.sleep(0.1)

    input("\nAny screen change? Press Enter...")

    # Try some specific 4-byte and 8-byte commands
    print("\n--- 4-byte commands ---")
    test_cmds = [
        (b'\x00\x00\x00\x00', "all zeros"),
        (b'\x01\x00\x00\x00', "cmd 0x01"),
        (b'\x02\x00\x00\x00', "cmd 0x02"),
        (b'\x10\x00\x00\x00', "cmd 0x10"),
        (b'\x40\x00\x00\x00', "cmd 0x40"),
        (b'\x80\x00\x00\x00', "cmd 0x80"),
        (b'\xFF\x00\x00\x00', "cmd 0xFF"),
        (b'\x00\x01\x00\x00', "byte1=0x01"),
        (b'\x00\x00\x01\x00', "byte2=0x01"),
        (b'\x00\x00\x00\x01', "byte3=0x01"),
    ]
    for data, label in test_cmds:
        if not write_and_check(label, data):
            return False
        time.sleep(0.1)

    input("\nAny screen change? Press Enter...")
    return True


def phase3_full_frame_raw(dev, ep_out, ep_in):
    """Try sending a full frame of raw pixel data."""
    print("\n" + "=" * 60)
    print("PHASE 3: Full frame raw pixel data")
    print("=" * 60)
    print("WigiDash is 1024x600. Trying full framebuffer writes.\n")

    has_in = ep_in is not None
    max_pkt = ep_out.wMaxPacketSize

    # 1024x600 in RGB565 = 1,228,800 bytes
    # 1024x600 in RGB888 = 1,843,200 bytes
    frame_rgb565 = 1024 * 600 * 2
    frame_rgb888 = 1024 * 600 * 3

    # Generate a solid red frame in RGB565
    red_565 = struct.pack('<H', 0xF800)

    # Try just blasting a full RGB565 frame
    print(f"--- Full RGB565 frame ({frame_rgb565} bytes) as solid red ---")
    frame = red_565 * (1024 * 600)
    print(f"  Sending {len(frame)} bytes in {max_pkt}-byte chunks...")

    total_written = 0
    start = time.time()
    for offset in range(0, len(frame), max_pkt):
        chunk = frame[offset:offset + max_pkt]
        written, err = try_write(dev, ep_out, chunk, timeout=2000)
        if err:
            elapsed = time.time() - start
            print(f"  Error at offset {offset} ({elapsed:.1f}s): {err}")
            if "no such device" in err.lower():
                return False
            break
        total_written += written

    elapsed = time.time() - start
    print(f"  Sent {total_written}/{len(frame)} bytes in {elapsed:.1f}s")

    if has_in:
        resp, rerr = try_read(dev, ep_in, 512, timeout=1000)
        if resp:
            print(f"  Response: {resp[:64].hex()}")

    input("\nAny change on screen? Press Enter...")

    # Try with a simple header: 4 bytes then frame data
    print(f"\n--- Header + RGB565 frame ---")
    for cmd_byte in [0x00, 0x01, 0x02]:
        header = struct.pack('<BBBB', cmd_byte, 0x00, 0x00, 0x00)
        first_chunk = header + frame[:max_pkt - 4]

        print(f"  cmd=0x{cmd_byte:02x}: sending header + frame...")
        written, err = try_write(dev, ep_out, first_chunk, timeout=2000)
        if err:
            print(f"    First chunk error: {err}")
            if "no such device" in err.lower():
                return False
            continue

        total_written = written
        for offset in range(max_pkt - 4, len(frame), max_pkt):
            chunk = frame[offset:offset + max_pkt]
            written, err = try_write(dev, ep_out, chunk, timeout=2000)
            if err:
                print(f"    Error at offset {offset}: {err}")
                break
            total_written += written

        print(f"    Sent {total_written} bytes total")

        if has_in:
            resp, rerr = try_read(dev, ep_in, 512, timeout=1000)
            if resp:
                print(f"    Response: {resp[:64].hex()}")

        time.sleep(0.2)

    input("\nAny change? Press Enter...")
    return True


def phase4_windowed_writes(dev, ep_out, ep_in):
    """Try sending pixel data with window/region coordinates."""
    print("\n" + "=" * 60)
    print("PHASE 4: Windowed region writes")
    print("=" * 60)
    print("Many display protocols set a window then stream pixels.\n")

    has_in = ep_in is not None

    # 10x10 red block in RGB565 = 200 bytes
    red_pixel = struct.pack('<H', 0xF800)
    pixels = red_pixel * (10 * 10)

    # Try various header formats for a 10x10 region at (0,0)
    headers = [
        # (label, header_bytes)
        # Format: cmd, x_start, y_start, x_end, y_end (16-bit LE)
        ("LE cmd=0x15 region 0,0->9,9",
         struct.pack('<BHHHH', 0x15, 0, 0, 9, 9)),
        ("LE cmd=0x15 region 0,0->10,10",
         struct.pack('<BHHHH', 0x15, 0, 0, 10, 10)),
        ("LE cmd=0x2C region 0,0->9,9",
         struct.pack('<BHHHH', 0x2C, 0, 0, 9, 9)),
        ("LE cmd=0x2C region 0,0->10,10",
         struct.pack('<BHHHH', 0x2C, 0, 0, 10, 10)),

        # Format: cmd, x, y, w, h (16-bit LE)
        ("LE cmd=0x00 xywh 0,0,10,10",
         struct.pack('<BHHHH', 0x00, 0, 0, 10, 10)),
        ("LE cmd=0x01 xywh 0,0,10,10",
         struct.pack('<BHHHH', 0x01, 0, 0, 10, 10)),

        # Same in big-endian
        ("BE cmd=0x15 region 0,0->9,9",
         struct.pack('>BHHHH', 0x15, 0, 0, 9, 9)),
        ("BE cmd=0x2C region 0,0->9,9",
         struct.pack('>BHHHH', 0x2C, 0, 0, 9, 9)),

        # 32-bit coordinates
        ("LE cmd=0x00 xywh32 0,0,10,10",
         struct.pack('<BIIII', 0x00, 0, 0, 10, 10)),

        # Packed: cmd(1) + length(4) + x(2) + y(2) + w(2) + h(2)
        ("cmd=0x00 len+xywh",
         struct.pack('<BIHHHH', 0x00, len(pixels), 0, 0, 10, 10)),
        ("cmd=0x01 len+xywh",
         struct.pack('<BIHHHH', 0x01, len(pixels), 0, 0, 10, 10)),
        ("cmd=0x02 len+xywh",
         struct.pack('<BIHHHH', 0x02, len(pixels), 0, 0, 10, 10)),
    ]

    for label, header in headers:
        data = header + pixels
        written, err = try_write(dev, ep_out, data, timeout=2000)
        if err:
            print(f"  {label}: err: {err}")
            if "no such device" in err.lower():
                return False
        else:
            print(f"  {label}: wrote {written}")
            if has_in:
                resp, rerr = try_read(dev, ep_in, 512, timeout=500)
                if resp:
                    print(f"    Response: {resp[:64].hex()}")
        time.sleep(0.1)

    input("\nAny change? Press Enter...")
    return True


def phase5_interrupt_check(dev):
    """Check if there are interrupt endpoints we should be polling."""
    print("\n" + "=" * 60)
    print("PHASE 5: Interrupt endpoint check")
    print("=" * 60)

    for cfg in dev:
        for intf in cfg:
            for ep in intf:
                ep_type = usb.util.endpoint_type(ep.bmAttributes)
                if ep_type == usb.util.ENDPOINT_TYPE_INTR:
                    direction = "IN" if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else "OUT"
                    print(f"\n  Found INTERRUPT EP 0x{ep.bEndpointAddress:02X} ({direction})")
                    if direction == "IN":
                        print(f"  Polling (interval={ep.bInterval})...")
                        for i in range(5):
                            try:
                                data = dev.read(ep.bEndpointAddress, ep.wMaxPacketSize, timeout=2000)
                                print(f"    Got {len(data)} bytes: {data.tobytes()[:32].hex()}")
                            except usb.core.USBError as e:
                                print(f"    Poll {i}: {e}")
                            time.sleep(0.1)

    print("  Done.")


def main():
    print("WigiDash Clean Probe Tool v3")
    print("=" * 40)
    print("No control transfers. Descriptors + bulk only.\n")

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("ERROR: WigiDash not found!")
        print("If it crashed, unplug/replug or wait for re-enumeration.")
        sys.exit(1)

    print(f"Found WigiDash: Bus {dev.bus} Device {dev.address}")

    # Dump descriptors first (before touching anything)
    endpoints_out, endpoints_in = dump_descriptors(dev)

    # Now claim the interface
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

    ep_out = endpoints_out[0] if endpoints_out else None
    ep_in = endpoints_in[0] if endpoints_in else None

    if not ep_out:
        print("\nERROR: No OUT endpoint found!")
        sys.exit(1)

    print(f"\nUsing OUT endpoint: 0x{ep_out.bEndpointAddress:02X} (max {ep_out.wMaxPacketSize})")
    if ep_in:
        print(f"Using IN endpoint:  0x{ep_in.bEndpointAddress:02X} (max {ep_in.wMaxPacketSize})")
    else:
        print("No IN endpoint found - device is write-only")

    try:
        if ep_in:
            phase1_check_inbound(dev, ep_in)
            input("\nReady for Phase 2? Press Enter...")

        ok = phase2_clean_bulk_writes(dev, ep_out, ep_in)
        if not ok:
            print("\nDevice crashed.")
            return

        input("\nReady for Phase 3 (full frame)? Press Enter...")
        ok = phase3_full_frame_raw(dev, ep_out, ep_in)
        if not ok:
            print("\nDevice crashed.")
            return

        input("\nReady for Phase 4 (windowed writes)? Press Enter...")
        ok = phase4_windowed_writes(dev, ep_out, ep_in)
        if not ok:
            print("\nDevice crashed.")
            return

        phase5_interrupt_check(dev)

        print("\n" + "=" * 60)
        print("ALL PHASES COMPLETE")
        print("=" * 60)
        print("Share the output and I'll analyze the protocol!")

    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    finally:
        try:
            usb.util.release_interface(dev, 0)
        except:
            pass


if __name__ == "__main__":
    main()
