#!/usr/bin/env python3
"""
WigiDash USB Probe Tool v2 - Gentle Edition
Start small, don't crash the device.
"""

import usb.core
import usb.util
import struct
import sys
import time

VENDOR_ID = 0x28DA
PRODUCT_ID = 0xEF01
EP_OUT = 0x01

def find_and_setup():
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("ERROR: WigiDash not found! (may need to wait for it to re-enumerate after crash)")
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
    
    print("Ready.\n")
    return dev

def safe_bulk_write(dev, data, timeout=2000):
    """Write with short timeout to avoid hanging."""
    try:
        written = dev.write(EP_OUT, data, timeout=timeout)
        return written, None
    except usb.core.USBError as e:
        return 0, str(e)

def safe_ctrl_read(dev, bRequestType, bRequest, wValue, wIndex, length, timeout=1000):
    """Control transfer read."""
    try:
        ret = dev.ctrl_transfer(bRequestType, bRequest, wValue, wIndex, length, timeout)
        return ret.tobytes(), None
    except usb.core.USBError as e:
        return None, str(e)

def safe_ctrl_write(dev, bRequestType, bRequest, wValue, wIndex, data=None, timeout=1000):
    """Control transfer write."""
    try:
        if data is None:
            data = b''
        ret = dev.ctrl_transfer(bRequestType, bRequest, wValue, wIndex, data, timeout)
        return ret, None
    except usb.core.USBError as e:
        return None, str(e)

def phase1_control_transfers(dev):
    """Probe control transfers first - these are safe and won't crash things."""
    print("="*60)
    print("PHASE 1: Control Transfer Discovery")
    print("="*60)
    print("(These are safe - the device will STALL unsupported requests)\n")
    
    # Vendor-specific device reads (bmRequestType = 0xC0)
    print("--- Vendor Device-to-Host reads (0xC0) ---")
    for req in range(0x00, 0x20):
        for val in [0x0000, 0x0001, 0x0100]:
            data, err = safe_ctrl_read(dev, 0xC0, req, val, 0, 256)
            if data is not None and len(data) > 0:
                print(f"  req=0x{req:02x} val=0x{val:04x}: [{len(data)} bytes] {data[:32].hex()}")
                if len(data) > 32:
                    print(f"    ... (truncated, full {len(data)} bytes)")
    
    # Vendor-specific interface reads (bmRequestType = 0xC1)
    print("\n--- Vendor Interface-to-Host reads (0xC1) ---")
    for req in range(0x00, 0x20):
        data, err = safe_ctrl_read(dev, 0xC1, req, 0, 0, 256)
        if data is not None and len(data) > 0:
            print(f"  req=0x{req:02x}: [{len(data)} bytes] {data[:32].hex()}")
    
    # Try some common vendor-specific requests
    print("\n--- Common vendor requests with various wIndex ---")
    for req in [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x10, 0x20, 0x30, 0x51, 0xA0, 0xFE, 0xFF]:
        for idx in [0, 1, 2]:
            data, err = safe_ctrl_read(dev, 0xC0, req, 0, idx, 256)
            if data is not None and len(data) > 0:
                print(f"  req=0x{req:02x} idx={idx}: [{len(data)} bytes] {data[:32].hex()}")
    
    print("\n--- Trying vendor write commands (0x40) with empty data ---")
    for req in range(0x00, 0x20):
        ret, err = safe_ctrl_write(dev, 0x40, req, 0, 0)
        if err is None or "stall" not in err.lower():
            if err and "pipe" in err.lower():
                continue  # STALL = not supported, skip
            print(f"  req=0x{req:02x}: ret={ret} err={err}")

def phase2_tiny_bulk_writes(dev):
    """Try very small bulk writes to see what the device accepts."""
    print("\n" + "="*60)
    print("PHASE 2: Tiny Bulk Writes")
    print("="*60)
    print("Sending very small packets to see what the device accepts.\n")
    
    # Try single bytes
    print("--- Single byte values ---")
    for b in range(0x00, 0x10):
        written, err = safe_bulk_write(dev, bytes([b]))
        status = f"wrote {written}" if err is None else f"ERROR: {err}"
        print(f"  0x{b:02x}: {status}")
        if err and "no such device" in err.lower():
            print("  DEVICE CRASHED! Stopping.")
            return False
        time.sleep(0.1)
    
    input("\nAny screen change? Press Enter...")
    
    # Try 2-byte commands
    print("\n--- Two byte values ---")
    for b1 in [0x00, 0x01, 0x02, 0x10, 0x40, 0x80, 0xFF]:
        for b2 in [0x00, 0x01, 0xFF]:
            written, err = safe_bulk_write(dev, bytes([b1, b2]))
            status = f"wrote {written}" if err is None else f"ERROR: {err}"
            if err and "no such device" in err.lower():
                print(f"  0x{b1:02x} 0x{b2:02x}: DEVICE CRASHED!")
                return False
            if err is None:
                print(f"  0x{b1:02x} 0x{b2:02x}: {status}")
            time.sleep(0.05)
    
    input("\nAny screen change? Press Enter...")
    return True

def phase3_structured_probes(dev):
    """Try more structured command patterns."""
    print("\n" + "="*60)
    print("PHASE 3: Structured Command Probes")
    print("="*60)
    
    # Pattern: many USB display protocols start with a length-prefixed command
    # or a specific magic byte sequence
    
    print("\n--- 4-byte headers (cmd + 3 bytes) ---")
    for cmd in [0x00, 0x01, 0x02, 0x03, 0x10, 0x11, 0x20, 0x40, 0x80, 0xAA, 0x55]:
        data = struct.pack('<BBBB', cmd, 0x00, 0x00, 0x00)
        written, err = safe_bulk_write(dev, data)
        if err and "no such device" in err.lower():
            print(f"  cmd 0x{cmd:02x} 00 00 00: CRASHED!")
            return False
        if err is None:
            print(f"  cmd 0x{cmd:02x} 00 00 00: wrote {written}")
        time.sleep(0.05)
    
    input("\nAny screen change? Press Enter...")
    
    # Try sending what looks like a "write region" command
    # with very small regions (e.g., 1x1 pixel)
    print("\n--- Small region writes (various header formats) ---")
    
    # Format A: [cmd, x16LE, y16LE, w16LE, h16LE, pixel_data...]
    # 1x1 pixel at (0,0), red in RGB565
    red565 = struct.pack('<H', 0xF800)
    
    for cmd in [0x00, 0x01, 0x02, 0x10, 0x11, 0x12, 0x20, 0x15, 0xC8]:
        header = struct.pack('<BHHHH', cmd, 0, 0, 1, 1)
        data = header + red565
        written, err = safe_bulk_write(dev, data)
        if err and "no such device" in err.lower():
            print(f"  Format A cmd=0x{cmd:02x}: CRASHED!")
            return False
        if err is None:
            print(f"  Format A cmd=0x{cmd:02x}: wrote {written} ({len(data)} bytes)")
        time.sleep(0.05)
    
    input("\nAny change? Press Enter...")
    
    # Format B: [cmd, x16BE, y16BE, w16BE, h16BE, pixel_data...]
    for cmd in [0x00, 0x01, 0x02, 0x10, 0x15]:
        header = struct.pack('>BHHHH', cmd, 0, 0, 1, 1)
        data = header + red565
        written, err = safe_bulk_write(dev, data)
        if err and "no such device" in err.lower():
            print(f"  Format B cmd=0x{cmd:02x}: CRASHED!")
            return False
        if err is None:
            print(f"  Format B cmd=0x{cmd:02x}: wrote {written} ({len(data)} bytes)")
        time.sleep(0.05)
    
    input("\nAny change? Press Enter...")
    
    # Format C: [32-bit length, then data] - some devices expect packet length first
    print("\n--- Length-prefixed data ---")
    payload = red565 * 100  # 200 bytes of red pixels
    for fmt in ['<I', '>I', '<H', '>H']:
        header = struct.pack(fmt, len(payload))
        data = header + payload
        written, err = safe_bulk_write(dev, data)
        if err and "no such device" in err.lower():
            print(f"  {fmt} length prefix: CRASHED!")
            return False
        if err is None:
            print(f"  {fmt} length prefix: wrote {written}")
        time.sleep(0.05)
    
    input("\nAny change? Press Enter...")
    
    # Format D: JPEG header? Check if it expects image files
    # JPEG magic: FF D8 FF
    print("\n--- Magic byte sequences ---")
    magics = [
        (b'\xFF\xD8\xFF', "JPEG header"),
        (b'\x89PNG', "PNG header"),
        (b'BM', "BMP header"),
        (b'WIGI', "WIGI magic?"),
        (b'GSKL', "GSKL magic?"),
        (b'\xAA\x55', "AA55 sync"),
        (b'\x55\xAA', "55AA sync"),
        (b'EL', "EL (ElmorLabs?)"),
    ]
    for magic, desc in magics:
        written, err = safe_bulk_write(dev, magic + b'\x00' * 16)
        if err and "no such device" in err.lower():
            print(f"  {desc}: CRASHED!")
            return False
        if err is None:
            print(f"  {desc}: wrote {written}")
        time.sleep(0.05)
    
    input("\nAny change? Press Enter...")
    return True

def phase4_512_byte_blocks(dev):
    """The endpoint max packet size is 512. Try 512-byte aligned writes."""
    print("\n" + "="*60)
    print("PHASE 4: 512-byte Aligned Blocks")
    print("="*60)
    print("The device may expect 512-byte aligned transfers.\n")
    
    # Send a single 512-byte block of a solid color
    # RGB565 red = 0xF800 = bytes F8 00
    block = (b'\x00\xF8' * 256)  # 512 bytes = 256 red pixels in RGB565 LE
    written, err = safe_bulk_write(dev, block)
    print(f"  512 bytes red RGB565: wrote={written} err={err}")
    if err and "no such device" in err.lower():
        return False
    
    input("Any change? Press Enter...")
    
    # Try 512-byte block with a header in the first few bytes
    # then pixel data for the rest
    for cmd in [0x00, 0x01, 0x02]:
        block = bytearray(512)
        # Header: cmd, then x=0, y=0, w=128, h=1 (128 pixels = 256 bytes at RGB565)
        struct.pack_into('<BHHHH', block, 0, cmd, 0, 0, 128, 1)
        # Fill rest with green pixels (0x07E0)
        for i in range(9, 512, 2):
            if i + 1 < 512:
                struct.pack_into('<H', block, i, 0x07E0)
        written, err = safe_bulk_write(dev, bytes(block))
        if err and "no such device" in err.lower():
            print(f"  512-byte block cmd=0x{cmd:02x}: CRASHED!")
            return False
        print(f"  512-byte block cmd=0x{cmd:02x}: wrote={written} err={err}")
        time.sleep(0.1)
    
    input("Any change? Press Enter...")
    return True

def phase5_jpeg_probe(dev):
    """Create a tiny valid JPEG and try sending it."""
    print("\n" + "="*60)
    print("PHASE 5: Try sending a small JPEG image")
    print("="*60)
    
    try:
        from PIL import Image
        import io
        
        # Create a small red image and encode as JPEG
        img = Image.new('RGB', (1024, 600), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=50)
        jpeg_data = buf.getvalue()
        print(f"  JPEG size: {len(jpeg_data)} bytes")
        
        # Try sending raw JPEG
        written, err = safe_bulk_write(dev, jpeg_data, timeout=5000)
        print(f"  Raw JPEG: wrote={written} err={err}")
        if err and "no such device" in err.lower():
            print("  CRASHED!")
            return False
        
        input("Any change on screen? Press Enter...")
        
        # Try with a length header
        header = struct.pack('<I', len(jpeg_data))
        written, err = safe_bulk_write(dev, header + jpeg_data, timeout=5000)
        print(f"  Length-prefixed JPEG: wrote={written} err={err}")
        if err and "no such device" in err.lower():
            print("  CRASHED!")
            return False
        
        input("Any change on screen? Press Enter...")
        
        # Try with various command + length headers
        for cmd in [0x01, 0x02, 0x10, 0x20]:
            header = struct.pack('<BI', cmd, len(jpeg_data))
            written, err = safe_bulk_write(dev, header + jpeg_data, timeout=5000)
            print(f"  cmd=0x{cmd:02x} + len + JPEG: wrote={written} err={err}")
            if err and "no such device" in err.lower():
                print("  CRASHED!")
                return False
            time.sleep(0.2)
        
        input("Any change? Press Enter...")
        
    except ImportError:
        print("  PIL not available, skipping JPEG test")
        print("  Install with: pip install Pillow")
    
    return True

def main():
    print("WigiDash Gentle Probe Tool v2")
    print("="*40)
    print("This will probe carefully without flooding the device.\n")
    
    dev = find_and_setup()
    
    try:
        phase1_control_transfers(dev)
        input("\nReady for Phase 2 (tiny bulk writes)? Press Enter...")
        
        ok = phase2_tiny_bulk_writes(dev)
        if not ok:
            print("\nDevice crashed. Wait for re-enumeration and re-run.")
            return
        
        ok = phase3_structured_probes(dev)
        if not ok:
            print("\nDevice crashed. Wait for re-enumeration and re-run.")
            return
        
        ok = phase4_512_byte_blocks(dev)
        if not ok:
            print("\nDevice crashed. Wait for re-enumeration and re-run.")
            return
        
        ok = phase5_jpeg_probe(dev)
        if not ok:
            print("\nDevice crashed. Wait for re-enumeration and re-run.")
            return
        
        print("\n" + "="*60)
        print("ALL PHASES COMPLETE")
        print("="*60)
        print("Please share all observations and I'll analyze the protocol!")
        
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    finally:
        try:
            usb.util.release_interface(dev, 0)
        except:
            pass

if __name__ == "__main__":
    main()
