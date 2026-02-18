#!/usr/bin/env python3
"""
WigiDash Driver - Based on reverse-engineered protocol from Linux binary.

Key discovery: The device uses CLASS-SPECIFIC control transfers (0x21/0xA1),
NOT vendor-specific (0x40/0xC0). That's why all our earlier probes failed.

Protocol:
1. Control transfer (0x21) to set up a command
2. Bulk transfer to send pixel data
3. Control transfer (0x21) to signal completion
"""

import usb.core
import usb.util
import struct
import sys
import time

VENDOR_ID = 0x28DA
PRODUCT_ID = 0xEF01
EP_OUT = 0x01

# bmRequestType values (class-specific, not vendor!)
RT_WRITE = 0x21  # Class, Host-to-Device, Interface
RT_READ  = 0xA1  # Class, Device-to-Host, Interface

# Command bytes extracted from the EUsbIf constructor command table
# The table at object+0x1238 maps indices to HID report IDs
CMD_TABLE = bytes([
    0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06,  # 0x1238
    0x07, 0x08, 0x09, 0x50, 0x51, 0x0F, 0x01, 0x06,  # 0x1240
    0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x60, 0x61,  # 0x1248
    0x62, 0x63, 0x64, 0x90, 0x91, 0x92, 0x93, 0x9F,  # 0x1250
    0x20, 0x21, 0x30, 0x01, 0x02, 0x31, 0x32, 0x00,  # 0x1258
    0x01, 0x33, 0x40, 0x41, 0x00, 0x04, 0x00, 0x00,  # 0x1260
])

# Named indices into the command table (based on which functions use which offsets)
# object+0x1238 is index 0 of CMD_TABLE
# The functions reference offsets from the object base:
#   0x1243 = CMD_TABLE[0x1243 - 0x1238] = CMD_TABLE[0x0B] = 0x50  -> GetBrightness
#   0x1244 = CMD_TABLE[0x0C] = 0x51  -> SetBrightness
#   0x1247 = CMD_TABLE[0x0F] = 0x06  -> Reset
#   0x124F = CMD_TABLE[23] = 0x61  -> WriteToWidget (setup)
#   0x1251 = CMD_TABLE[25] = 0x63  -> WriteToWidget (done)

CMD_GET_BRIGHTNESS = 0x50
CMD_SET_BRIGHTNESS = 0x51
CMD_RESET = 0x06
CMD_WRITE_SETUP = 0x61
CMD_WRITE_DONE = 0x63
CMD_UI = 0x70
CMD_CHECK_APP = 0x00  # Returns "WD\0" for app mode, "BL\0" for bootloader
CMD_GET_DEVICE_ID = 0x01
CMD_GET_FW_VERSION = 0x04
CMD_GET_DEVICE_UID = 0x05
CMD_GET_CONFIG = 0x10       # cmd_table[16] - reads 48 bytes of config
CMD_SET_CONFIG = 0x11       # cmd_table[17] - writes 48 bytes of config
CMD_CLEAR_SCREEN_TIMEOUT = 0x12  # cmd_table[18] - clears screen timeout
CMD_CLEAR_PAGE = 0x90    # cmd_table[27] - clears a page
CMD_ADD_WIDGET = 0x91    # cmd_table[28] - adds/configures a widget
CMD_REMOVE_WIDGET = 0x92 # cmd_table[29] - removes a widget
CMD_MOVE_WIDGET = 0x93   # cmd_table[30] - moves a widget

# Frame buffer size: 1024 * 592 * 2 = 1,212,416 bytes
# Widget height is 592, not 600 (8 pixels reserved?)
FRAME_SIZE = 0x128000  # 1,212,416 bytes
WIDGET_WIDTH = 1024
WIDGET_HEIGHT = 592  # NOT 600!

TIMEOUT = 2000

class WigiDash:
    def __init__(self):
        self.dev = None
        self.ep_out = EP_OUT

    def connect(self):
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            print("ERROR: WigiDash not found!")
            return False

        print(f"Found WigiDash: Bus {self.dev.bus} Device {self.dev.address}")
        print(f"  Serial: {self.dev.serial_number}")

        if self.dev.is_kernel_driver_active(0):
            self.dev.detach_kernel_driver(0)
        try:
            self.dev.set_configuration()
        except:
            pass
        try:
            usb.util.claim_interface(self.dev, 0)
        except:
            pass

        return True

    def ctrl_write(self, bRequest, wValue=0, data=None, length=0):
        """Class-specific control write (bmRequestType=0x21)."""
        try:
            if data is None:
                data = b''
            ret = self.dev.ctrl_transfer(RT_WRITE, bRequest, wValue, 0, data, TIMEOUT)
            return True, ret
        except usb.core.USBError as e:
            return False, str(e)

    def ctrl_read(self, bRequest, wValue=0, length=1):
        """Class-specific control read (bmRequestType=0xA1)."""
        try:
            ret = self.dev.ctrl_transfer(RT_READ, bRequest, wValue, 0, length, TIMEOUT)
            return True, ret.tobytes()
        except usb.core.USBError as e:
            return False, str(e)

    def bulk_write(self, data, timeout=TIMEOUT):
        """Bulk write to EP_OUT."""
        try:
            written = self.dev.write(self.ep_out, data, timeout=timeout)
            return written, None
        except usb.core.USBError as e:
            return 0, str(e)

    def get_brightness(self):
        """Read current brightness (0-100)."""
        ok, data = self.ctrl_read(CMD_GET_BRIGHTNESS, 0, 1)
        if ok:
            return data[0]
        print(f"GetBrightness failed: {data}")
        return None

    def set_brightness(self, level):
        """Set brightness (0-100)."""
        level = max(0, min(100, level))
        # Binary sends exactly 1 byte, not a 4-byte int
        ok, ret = self.ctrl_write(CMD_SET_BRIGHTNESS, 0, bytes([level]))
        if not ok:
            print(f"SetBrightness failed: {ret}")
        return ok

    def reset(self, mode=0):
        """Reset the device."""
        ok, ret = self.ctrl_write(CMD_RESET, mode)
        if not ok:
            print(f"Reset failed: {ret}")
        return ok

    def send_ui_cmd(self, cmd_value):
        """Send a UI command."""
        ok, ret = self.ctrl_write(CMD_UI, cmd_value)
        if not ok:
            print(f"SendUiCmd failed: {ret}")
        return ok

    def go_to_screen(self, screen, page=0):
        """Switch to a screen.
        screen: 0=init, 1=widget, 2=?, 3=?
        """
        if screen == 0:
            value = 0x01
        elif screen == 1:
            value = (page * 4) | 0x20
        elif screen == 2:
            value = (page * 4) | 0x21
        elif screen == 3:
            value = (page * 4) | 0x22
        else:
            value = screen
        return self.send_ui_cmd(value)

    def check_app_mode(self):
        """Check if device is in app mode (vs bootloader).
        Returns True for app mode ('WD'), False for bootloader ('BL'), None on error.
        """
        ok, data = self.ctrl_read(CMD_CHECK_APP, 0, 3)
        if not ok:
            print(f"CheckAppMode failed: {data}")
            return None
        if len(data) >= 2:
            mode = chr(data[0]) + chr(data[1])
            if mode == 'WD':
                return True
            elif mode == 'BL':
                return False
        return None

    def clear_screen_timeout(self):
        """Clear screen timeout - called during binary's initialization."""
        ok, ret = self.ctrl_write(CMD_CLEAR_SCREEN_TIMEOUT, 0)
        if not ok:
            print(f"ClearScreenTimeout failed: {ret}")
        return ok

    def get_config(self):
        """Read device config (48 bytes)."""
        ok, data = self.ctrl_read(CMD_GET_CONFIG, 0, 48)
        if not ok:
            print(f"GetConfig failed: {data}")
            return None
        return data

    def set_config(self, config_data):
        """Write device config (48 bytes)."""
        ok, ret = self.ctrl_write(CMD_SET_CONFIG, 0, config_data)
        if not ok:
            print(f"SetConfig failed: {ret}")
        return ok

    def clear_page(self, page_idx=0):
        """Clear a page - sends cmd 0x90.
        Must be called before GoToScreen/AddWidget.
        """
        ok, ret = self.ctrl_write(CMD_CLEAR_PAGE, page_idx)
        if not ok:
            print(f"ClearPage failed: {ret}")
        return ok

    def add_widget(self, page_idx=0, widget_idx=0, x=0, y=0, w=WIDGET_WIDTH, h=WIDGET_HEIGHT):
        """Add/configure a widget - sends cmd 0x91 with 20-byte config.
        Must be called before WriteToWidget so the device knows the dimensions.
        Binary uses: x=0, y=0, w=1024, h=592.
        """
        # Build 20-byte _WidgetConfig struct (matching binary layout)
        config = struct.pack('<HHHH', x, y, w, h)  # 8 bytes: x, y, w, h
        config += b'\x00' * 12  # 12 bytes of zeros (remaining fields)
        wValue = ((page_idx & 0xFF) << 8) | (widget_idx & 0xFF)
        ok, ret = self.ctrl_write(CMD_ADD_WIDGET, wValue, config)
        if not ok:
            print(f"AddWidget failed: {ret}")
        return ok

    def initialize(self):
        """Full initialization matching the binary's Wigidash_worker::initialize().
        1. CheckAppMode
        2. GetFirmwareString
        3. GetConfig
        4. ClearScreenTimeout
        5. SetConfig (write config back)
        6. ClearPage(0)
        7. GoToScreen(1)
        8. AddWidget(0, 0, config)
        """
        print("  Initialization sequence (matching binary):")

        # 1. Check app mode
        app_mode = self.check_app_mode()
        if app_mode is not True:
            print("    CheckAppMode: NOT in app mode!")
            return False
        print("    CheckAppMode: WD (app mode) - OK")

        # 2. Get firmware string
        ok, fw = self.ctrl_read(CMD_GET_FW_VERSION, 0, 64)
        if ok:
            print(f"    FW: {fw.decode('ascii', errors='replace').rstrip(chr(0))}")

        # 3. Get config
        config = self.get_config()
        if config:
            print(f"    GetConfig: {config.hex()}")
        else:
            print("    GetConfig: FAILED")

        # 4. Clear screen timeout
        ok = self.clear_screen_timeout()
        print(f"    ClearScreenTimeout: {'OK' if ok else 'FAILED'}")

        # 5. Set config back (write the same config we read)
        if config:
            ok = self.set_config(config)
            print(f"    SetConfig: {'OK' if ok else 'FAILED'}")

        # 6. ClearPage(0) - prepare the page
        ok = self.clear_page(0)
        print(f"    ClearPage(0): {'OK' if ok else 'FAILED'}")
        if not ok:
            return False

        # 7. GoToScreen(1) - switch to widget display
        ok = self.go_to_screen(1)
        print(f"    GoToScreen(1): {'OK' if ok else 'FAILED'}")
        if not ok:
            return False

        # 8. AddWidget(0, 0) - configure widget dimensions (1024x592)
        ok = self.add_widget(0, 0)
        print(f"    AddWidget(0,0, 1024x592): {'OK' if ok else 'FAILED'}")
        if not ok:
            return False

        return True

    def write_to_widget(self, page_idx, widget_idx, pixel_data):
        """Write pixel data to a widget.
        1. Control write to announce the transfer
        2. Bulk write the pixel data
        3. Control write to signal completion

        The binary always sends exactly FRAME_SIZE (0x128000) bytes.
        """
        # Pad or truncate to exact frame size
        if len(pixel_data) < FRAME_SIZE:
            pixel_data = pixel_data + b'\x00' * (FRAME_SIZE - len(pixel_data))
        elif len(pixel_data) > FRAME_SIZE:
            pixel_data = pixel_data[:FRAME_SIZE]

        # Step 1: Setup control transfer
        # wValue encodes page and widget index
        wValue = ((page_idx & 0xFF) << 8) | (widget_idx & 0xFF)
        # Setup data: [param3 as int32 (always 0), size as int32]
        setup_data = struct.pack('<II', 0, FRAME_SIZE)

        print(f"  Setup: cmd=0x{CMD_WRITE_SETUP:02X} wValue=0x{wValue:04X} data={setup_data.hex()}")
        ok, ret = self.ctrl_write(CMD_WRITE_SETUP, wValue, setup_data)
        if not ok:
            print(f"  Setup failed: {ret}")
            return False

        # Small delay to let device process setup command
        time.sleep(0.01)

        # Step 2: Bulk write pixel data (matching binary: 2000ms timeout)
        print(f"  Sending {len(pixel_data)} bytes of pixel data...")
        written, err = self.bulk_write(pixel_data, timeout=5000)
        if err:
            print(f"  Bulk write failed at {written} bytes: {err}")
            return False
        print(f"  Wrote {written} bytes")

        # Step 3: Done control transfer
        print(f"  Done: cmd=0x{CMD_WRITE_DONE:02X}")
        ok, ret = self.ctrl_write(CMD_WRITE_DONE, 0)
        if not ok:
            print(f"  Done signal failed: {ret}")
            return False

        return True


def test_safe_reads(wigi):
    """Read only known-safe control commands."""
    print("=" * 60)
    print("TEST 1: Device info and status")
    print("=" * 60)

    # Check app mode first (like the binary does)
    print("\n--- CheckAppMode (cmd 0x00) ---")
    app_mode = wigi.check_app_mode()
    if app_mode is True:
        print("  Device is in APP mode (WD) - good!")
    elif app_mode is False:
        print("  Device is in BOOTLOADER mode (BL) - need to switch!")
    else:
        print("  Could not determine mode")

    print("\n--- GetBrightness (cmd 0x50) ---")
    brightness = wigi.get_brightness()
    if brightness is not None:
        print(f"  Current brightness: {brightness}")

    print("\n--- Firmware version (cmd 0x04) ---")
    ok, data = wigi.ctrl_read(CMD_GET_FW_VERSION, 0, 64)
    if ok:
        print(f"  {data.decode('ascii', errors='replace').rstrip(chr(0))}")

    print("\n--- Device UID (cmd 0x05) ---")
    ok, data = wigi.ctrl_read(CMD_GET_DEVICE_UID, 0, 13)
    if ok:
        print(f"  {data.hex()}")


def test_brightness(wigi):
    """Test brightness control."""
    print("\n" + "=" * 60)
    print("TEST 2: Brightness control")
    print("=" * 60)

    orig = wigi.get_brightness()
    print(f"  Current brightness: {orig}")

    for level in [100, 25, 75]:
        print(f"  Setting brightness to {level}...")
        ok = wigi.set_brightness(level)
        time.sleep(0.5)
        readback = wigi.get_brightness()
        print(f"    Set={'OK' if ok else 'FAIL'}, readback={readback}")
        time.sleep(1)

    if orig is not None:
        print(f"  Restoring brightness to {orig}...")
        wigi.set_brightness(orig)


def test_go_to_screen(wigi):
    """Try switching screens."""
    print("\n" + "=" * 60)
    print("TEST 3: GoToScreen")
    print("=" * 60)

    print("\n  Sending GoToScreen(1) - widget mode...")
    ok = wigi.go_to_screen(1)
    print(f"  Result: {'OK' if ok else 'FAILED'}")

    input("  Any screen change? Press Enter...")


def test_write_red_screen(wigi):
    """Try writing a full red screen with full initialization.
    Now includes ClearPage + AddWidget (discovered from binary analysis).
    """
    print("\n" + "=" * 60)
    print("TEST 4: Write full red screen (with full init)")
    print("=" * 60)
    print("  Init now includes: ClearPage(0) + GoToScreen(1) + AddWidget(0,0)")

    # Full initialization matching the binary
    # (now includes ClearPage, GoToScreen, and AddWidget)
    print("\n  Running binary-matching initialization...")
    ok = wigi.initialize()
    if not ok:
        print("  Initialization FAILED!")
        return
    time.sleep(0.5)

    # Create full red frame (RGB565) - use FRAME_SIZE (1024*592*2)
    red_pixel = struct.pack('<H', 0xF800)
    frame = red_pixel * (FRAME_SIZE // 2)
    print(f"  Frame size: {len(frame)} bytes (0x{len(frame):X})")

    # Write to widget 0, page 0
    print("\n  Writing red screen to widget 0, page 0...")
    ok = wigi.write_to_widget(0, 0, frame)
    print(f"  Result: {'OK' if ok else 'FAILED'}")

    input("  Any screen change? Press Enter...")


def main():
    quick = '--quick' in sys.argv or '-q' in sys.argv

    print("WigiDash Driver Test")
    print("=" * 40)
    print("Using class-specific control transfers (0x21/0xA1)")
    if quick:
        print("QUICK MODE: skipping to pixel write test")
    print()

    wigi = WigiDash()
    if not wigi.connect():
        sys.exit(1)

    try:
        if not quick:
            test_safe_reads(wigi)

            input("\nReady for brightness test? Press Enter...")
            test_brightness(wigi)

            input("\nReady for screen test? Press Enter...")
            test_go_to_screen(wigi)

            input("\nReady for red screen write? Press Enter...")

        test_write_red_screen(wigi)

        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETE")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    finally:
        try:
            usb.util.release_interface(wigi.dev, 0)
        except:
            pass


if __name__ == "__main__":
    main()
