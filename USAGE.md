# WigiDash Usage Guide

A Linux utility for the G.Skill WigiDash 7" USB display (VID 0x28DA, PID 0xEF01).

## Requirements

- Python 3.8+
- libusb1 (system package, e.g. `libusb-1.0-0`)
- A monospace TTF font (DejaVu Sans Mono recommended)

### Python Dependencies

| Package | Purpose |
|---|---|
| pyusb | USB device communication |
| Pillow | Image rendering and drawing |
| psutil | System metrics (CPU, memory, disk, network) |
| numpy | Fast RGB565 pixel conversion |
| dbus-python | Sleep/wake detection (optional) |
| PyGObject | GLib main loop for D-Bus (optional) |

Install all dependencies:
```bash
pip install -r requirements.txt
```

## Commands

All commands require root (or a udev rule — see [README.md](README.md#udev-rule)).

### System Monitor

```bash
sudo ./wigidash.py monitor
sudo ./wigidash.py monitor --interval 0.5
```

Runs the live graphical dashboard. Press Ctrl+C to stop.

### Display an Image

```bash
sudo ./wigidash.py image photo.jpg
sudo ./wigidash.py image wallpaper.png --fit crop
sudo ./wigidash.py image logo.png --fit contain --bg 1a1a2e
```

Fit modes:
- `contain` (default) — fit entire image, letterboxed with background color
- `crop` — fill the display, cropping edges as needed
- `stretch` — stretch to fill (may distort)

Supported formats: PNG, JPEG, BMP, GIF, TIFF, WebP, and anything Pillow supports.

### Solid Color

```bash
sudo ./wigidash.py color ff0000    # Red
sudo ./wigidash.py color 000000    # Black
```

### Brightness

```bash
sudo ./wigidash.py brightness 75   # Set to 75%
sudo ./wigidash.py off              # Brightness 0
```

## Dashboard Layout

The monitor displays a 3x2 grid of panels on a dark theme:

```
┌──────────────────────────────────────────────────────────┐
│  hostname              up 3d 12h 4m              14:32:05│
├──────────────┬──────────────┬────────────────────────────┤
│     CPU      │     GPU      │      Temperatures          │
│   ╭─────╮    │   ╭─────╮    │  ● CPU        142°F        │
│   │ 23% │    │   │ 45% │    │  ● GPU        131°F        │
│   ╰─────╯    │   ╰─────╯    │  ● NVMe 1      95°F        │
│  Clock  4200 │  Clock  2100 │  ● Coolant     82°F        │
│  Temp   142° │  VRAM 4/24GB │                             │
│  Load   1.23 │  Power  180W │                             │
├──────────────┼──────────────┼────────────────────────────┤
│    Memory    │    Network   │       Storage               │
│  RAM  62.3%  │  Up  1.2MB/s │  /         45.2%            │
│  ████████░░  │  ▂▃▅▇▆▄▃▂▁  │  ████████░░░                │
│  Swap  0.0%  │  Dn  5.4MB/s │  120.4/500.0 GB             │
│  Power  380W │  ▁▂▃▅▇▆▅▃▂  │  /home     72.1%            │
│  CPU 95W GPU │  Top Procs   │  ████████████░░              │
│  +12V   340W │  firefox 12% │  1440.2/2000.0 GB           │
└──────────────┴──────────────┴────────────────────────────┘
```

### Panel Details

**CPU** — Arc gauge showing overall CPU utilization. Below: clock speed (MHz), temperature, load average, and core count.

**GPU** — Arc gauge for NVIDIA GPU utilization via NVML (ctypes, no subprocess). Shows clock speed, VRAM usage, power draw, and temperature. Displays "N/A" if no NVIDIA GPU is detected.

**Temperatures** — Color-coded list of sensor readings (green < 60°C, yellow < 80°C, red >= 80°C). Sources: k10temp/coretemp (CPU), amdgpu/nvidia (GPU), nvme, coolant/liquid sensors.

**Memory & Power** — RAM and swap usage with progress bars. Below: total system power from Corsair iLink PSU (via `corsairpsu` hwmon driver), CPU package power via Intel RAPL, GPU power via NVML, and per-rail (+12V, +5V, +3.3V) wattage and voltage. PSU fan speed and VRM/case temperatures at the bottom.

**Network & Processes** — Upload and download rates with sparkline area charts (60-sample history). Below: top 3 processes sorted by CPU usage.

**Storage** — Per-partition usage bars for real filesystems (ext4, btrfs, xfs, ntfs, etc.). Filters out virtual filesystems, boot partitions, and snap mounts.

## Architecture

### Background Data Collection

The monitor uses a two-thread architecture to keep the display responsive:

- **Collector thread** — runs every 1 second, gathering slow sensor data (CPU %, frequencies, temperatures, GPU stats, PSU readings, disk partitions, top processes) using a `ThreadPoolExecutor` for parallel I/O
- **Render thread** (main) — runs at the configured interval (default 0.5s), updating only network rates (which need high-frequency sampling) and compositing the frame from cached data

This means the display updates at ~2 FPS while expensive sensors (temperature scans, process listings) only run once per second.

### Sleep/Wake Handling

When `dbus-python` and `PyGObject` are installed, the monitor listens for `PrepareForSleep` signals from `org.freedesktop.login1.Manager` on the system D-Bus:

- **Sleep** — sets brightness to 0 and pauses rendering
- **Wake** — reconnects the USB device (which re-enumerates after suspend) and resumes

Without these optional dependencies, the monitor still handles USB disconnects gracefully via its reconnect loop.

### Sensor Sources

| Metric | Source |
|---|---|
| CPU utilization, frequency | `psutil` |
| CPU temperature | `psutil.sensors_temperatures()` (k10temp/coretemp) |
| CPU power | Intel RAPL (`/sys/class/powercap/intel-rapl:0/energy_uj`) |
| GPU utilization, VRAM, power, temp | NVIDIA NVML via ctypes (`libnvidia-ml.so.1`) |
| PSU power, voltage, temp, fan | `corsairpsu` hwmon (`/sys/class/hwmon/*/name`) |
| Memory, swap | `psutil` |
| Network I/O | `psutil.net_io_counters()` |
| Disk partitions | `psutil.disk_partitions()` + `disk_usage()` |
| Temperatures | `psutil.sensors_temperatures()` |
| Top processes | `psutil.process_iter()` |

## Running as a Service

See `install.sh` for automated setup. Manual steps:

```bash
# Copy script
sudo mkdir -p /opt/wigidash
sudo cp wigidash.py /opt/wigidash/
sudo chmod +x /opt/wigidash/wigidash.py

# Install service
sudo cp wigidash.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wigidash
```

The service runs as root (needed for USB device access, RAPL, and PSU hwmon) and restarts on failure with a 5-second delay.

## Display Specs

- Resolution: 1024 x 592 pixels (widget area; physical panel is 1024x600)
- Color format: RGB565 (16-bit, 65536 colors)
- Interface: USB 2.0 bulk transfer
- Frame size: 1,212,416 bytes per frame

## Protocol Reference

See [wigidash-protocol.md](wigidash-protocol.md) for the full reverse-engineered USB protocol documentation.

## Files

| File | Description |
|---|---|
| `wigidash.py` | Main utility (driver, dashboard, image display, CLI) |
| `wigidash-driver.py` | Standalone low-level driver with test suite |
| `wigidash-debug.py` | Isolated debug tests for protocol exploration |
| `wigidash-protocol.md` | Reverse-engineered USB protocol documentation |
| `wigidash.service` | Systemd unit file |
| `install.sh` | Install/uninstall script |
| `requirements.txt` | Python dependencies |
| `LICENSE` | GPL-3.0 license |
| `USAGE.md` | This file |
| `README.md` | Project overview and quick start |
