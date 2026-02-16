# WigiDash

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A Linux driver and graphical dashboard for the **G.Skill WigiDash** 7" USB display panel.

![WigiDash dashboard running](dash.png)

## Features

- **Graphical dashboard** with arc gauges, sparkline graphs, and color-coded panels
- **CPU monitoring** — utilization arc gauge, clock speed, temperature, load average
- **GPU monitoring** — NVIDIA GPU via NVML (utilization, VRAM, power, temperature)
- **Corsair iLink PSU** — per-rail power/voltage, VRM/case temps, fan RPM (via hwmon)
- **CPU power** — package wattage via Intel RAPL
- **Memory & swap** — usage bars with percentages
- **Network** — upload/download sparkline history graphs
- **Storage** — per-partition usage bars
- **Top processes** — sorted by CPU usage
- **Temperature overview** — CPU, GPU, NVMe, coolant sensors with colored indicators
- **Sleep/wake handling** — automatic suspend/resume via systemd-logind D-Bus
- **Image display** — show any image file (PNG, JPEG, BMP, etc.) with fit modes
- **Brightness control** — set display brightness 0-100%

## Quick Start

### Dependencies

```bash
# System packages
sudo pacman -S libusb python-pillow python-pyusb python-psutil python-numpy  # Arch
sudo apt install libusb-1.0-0 python3-pil python3-usb python3-psutil python3-numpy  # Debian/Ubuntu

# Or via pip
pip install -r requirements.txt
```

Optional (for sleep/wake detection):
```bash
pip install dbus-python PyGObject
```

### udev Rule

Allow non-root access to the device:

```bash
sudo tee /etc/udev/rules.d/99-wigidash.rules << 'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef01", MODE="0666"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Run

```bash
sudo ./wigidash.py monitor              # Live dashboard
sudo ./wigidash.py image photo.jpg      # Display an image
sudo ./wigidash.py brightness 75        # Set brightness
```

## Systemd Service

Install as a system service that starts on boot:

```bash
sudo bash install.sh
```

This installs the script to `/opt/wigidash/`, sets up the udev rule, and enables the systemd service.

```bash
systemctl status wigidash       # Check status
journalctl -u wigidash -f       # View logs
sudo bash install.sh --uninstall  # Clean removal
```

## Hardware

- **Device**: G.Skill WigiDash PC Panel (7" IPS, 1024x600)
- **USB**: VID `0x28DA`, PID `0xEF01`, vendor-specific class
- **Display area**: 1024x592 pixels (widget region)
- **Color**: RGB565 (16-bit, 65536 colors)
- **Interface**: USB 2.0 bulk transfer + class-specific control transfers

## Documentation

- [USAGE.md](USAGE.md) — detailed usage guide and architecture overview
- [wigidash-protocol.md](wigidash-protocol.md) — reverse-engineered USB protocol documentation

## License

This project is licensed under the GNU General Public License v3.0 — see the [LICENSE](LICENSE) file.
