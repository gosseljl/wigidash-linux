# WigiDash Protocol - Reverse Engineered from Linux Binary

## Transport Layer
- USB Vendor-specific class (255/0/0)
- VID: 0x28DA, PID: 0xEF01
- Single bulk OUT endpoint (0x01, 512-byte max packet)
- Uses BOTH control transfers (bmRequestType 0x21/0xA1) AND bulk transfers

## Control Transfers (NOT vendor 0x40/0xC0!)
The device uses **class-specific** control transfers:
- Write: bmRequestType = 0x21 (class, host-to-device, interface)
- Read:  bmRequestType = 0xA1 (class, device-to-host, interface)
- Timeout: 2000ms (0x7D0)

This is why our 0xC0/0x40 probes all failed! The device uses 0x21/0xA1.

## Command Byte Table (from EUsbIf constructor)
Stored at object+0x1238, command IDs are stored as single bytes:

Offset 0x1238: 00 00 01 02 03 04 05 06  (indices 0-7)
Offset 0x1240: 07 08 09 50 51 0F 01 06  (indices 8-15)
Offset 0x1248: 10 11 12 13 14 15 60 61  (indices 16-23)
Offset 0x1250: 62 63 64 90 91 92 93 9F  (indices 24-31)
Offset 0x1258: 20 21 30 01 02 31 32 00  (indices 32-39)
Offset 0x1260: 01 33 40 41 00 04 00 00  (indices 40-47)

## Key Functions and Their Command Bytes

### WriteToWidget(page_idx, widget_idx, ?, pixel_data*, size)
1. Sends control write (0x21) with:
   - bRequest = 0x61 (cmd_table[0x124F], index 23)
   - wValue = (page_idx << 8) | widget_idx
   - data = 8 bytes: [param3 as int32 (always 0), size as int32]
2. Sends pixel data via bulk transfer (WinUsb_Bulk_Write)
3. Sends another control write with bRequest = 0x63 (cmd_table[0x1251], index 25)

### Key protocol: Control transfer FIRST, then bulk data!

### GoToScreen(DeviceScreen)
Sends SendUiCmd with:
- bRequest = 0x70
- wValue = screen_id (computed from page number)
  - Screen 0: value = 0x01
  - Screen 1: value = (page*4) | 0x20
  - Screen 2: value = (page*4) | 0x21
  - Screen 3: value = (page*4) | 0x22

### Reset(mode)
- bRequest = cmd_table[0x1247]
- wValue = mode

### SetBrightness(level)
- bRequest = cmd_table[0x1244]
- wValue = 0
- data = 1 byte (brightness 0-100)

### GetBrightness()
- bRequest = cmd_table[0x1243] (read via 0xA1)
- wValue = 0
- reads 1 byte

### CheckAppMode()
- Used during initialization to check device state

### ClearPage(page_idx)
- bRequest = 0x90 (cmd_table[27])
- wValue = page_idx
- No data
- Must be called before GoToScreen/AddWidget

### AddWidget(page_idx, widget_idx, widget_config)
- bRequest = 0x91 (cmd_table[28])
- wValue = (page_idx << 8) | widget_idx
- data = 20 bytes _WidgetConfig struct:
  - uint16 x = 0 (widget x position)
  - uint16 y = 0 (widget y position)
  - uint16 w = 1024 (widget width)
  - uint16 h = 592 (widget height - NOT 600!)
  - 12 bytes of zeros (other fields)
- CRITICAL: Must be called before WriteToWidget!

### RemoveWidget(page_idx, widget_idx)
- bRequest = 0x92 (cmd_table[29])

### MoveWidget(page_idx, widget_idx, x, y)
- bRequest = 0x93 (cmd_table[30])

## Frame Buffer
- 1024x600 display panel, but widget area is 1024x592
- RGB565 format (2 bytes per pixel)
- Full frame = 1024 * 592 * 2 = 1,212,416 bytes (0x128000)
- The rgb_worker copies scanlines into a widget_image_vec buffer
- Uses row stride of 0x400 (1024) pixels

## Data Flow (COMPLETE initialization + write sequence)
1. CheckAppMode() - verify device is in app mode ("WD")
2. GetFirmwareString() - read firmware version
3. GetConfig() - read 48 bytes of config
4. ClearScreenTimeout() - cmd 0x12
5. SetConfig() - write config back
6. ClearPage(0) - cmd 0x90, prepare the page
7. GoToScreen(1) - cmd 0x70, switch to widget display mode
8. AddWidget(0, 0, config) - cmd 0x91, configure widget dimensions
9. WriteToWidget(page, widget, 0, pixel_data, 0x128000)
   - First: control transfer 0x61 to announce the write
   - Then: bulk transfer of pixel data
   - Finally: control transfer 0x63 to signal completion
