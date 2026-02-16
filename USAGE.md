# WigiDash Display Utility

A Linux utility for the G.Skill WigiDash 7" USB display (VID 0x28DA, PID 0xEF01).

Supports displaying images, solid colors, and a live system monitor dashboard.

## Requirements

- Python 3.8+
- pyusb (`pip install pyusb`)
- Pillow (`pip install Pillow`)
- psutil (`pip install psutil`) - for system monitor mode
- numpy (`pip install numpy`)
- libusb1 (system package, e.g. `libusb-1.0-0`)
- A monospace TTF font (DejaVu Sans Mono recommended)

Install all Python deps:
```
pip install pyusb Pillow psutil numpy
```

## Usage

All commands require root (or a udev rule, see below).

### Display an Image

```bash
sudo ./wigidash.py image photo.jpg
sudo ./wigidash.py image wallpaper.png --fit crop
sudo ./wigidash.py image logo.png --fit contain --bg 1a1a2e
```

Fit modes:
- `contain` (default) - Fit entire image within the display, letterboxed with background color
- `crop` - Fill the display, cropping edges as needed
- `stretch` - Stretch to fill (may distort)

Supported formats: PNG, JPEG, BMP, GIF, TIFF, WebP, and anything Pillow supports.

### System Monitor

```bash
sudo ./wigidash.py monitor
sudo ./wigidash.py monitor --interval 0.5
```

Displays a live dashboard showing:
- Hostname, uptime, and current time
- Overall and per-core CPU usage with color-coded bars
- System load averages
- RAM and swap usage
- Network upload/download rates
- Disk read/write rates
- Temperature readings (CPU, GPU, etc. if available)
- Top processes sorted by CPU usage

Press Ctrl+C to stop.

### Solid Color

```bash
sudo ./wigidash.py color ff0000    # Red
sudo ./wigidash.py color 00ff00    # Green
sudo ./wigidash.py color 0000ff    # Blue
sudo ./wigidash.py color 000000    # Black
```

### Brightness

```bash
sudo ./wigidash.py brightness 75   # Set to 75%
sudo ./wigidash.py brightness 0    # Off
sudo ./wigidash.py off              # Same as brightness 0
```

## Display Specs

- Resolution: 1024 x 592 pixels (widget area; panel is 1024x600)
- Color format: RGB565 (16-bit, 65536 colors)
- Interface: USB 2.0 bulk transfer
- Frame size: 1,212,416 bytes per frame

## udev Rule (Optional)

To avoid running as root, create a udev rule:

```bash
sudo tee /etc/udev/rules.d/99-wigidash.rules << 'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef01", MODE="0666"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and replug the device.

## Protocol Reference

See `wigidash-protocol.md` for the full reverse-engineered USB protocol documentation.

## Files

| File | Description |
|---|---|
| `wigidash.py` | Main utility (image display, monitor, color, brightness) |
| `wigidash-driver.py` | Low-level driver with test suite |
| `wigidash-debug.py` | Isolated debug tests |
| `wigidash-protocol.md` | Protocol documentation |
| `USAGE.md` | This file |
