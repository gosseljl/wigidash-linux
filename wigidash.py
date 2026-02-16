#!/usr/bin/env python3
"""
WigiDash - Display utility for the G.Skill WigiDash USB display.

Commands:
  image <file>       Display an image (PNG, JPEG, BMP, etc.)
  monitor            Live system monitor (htop-like dashboard)
  color <hex>        Fill screen with a solid color (e.g. ff0000)
  brightness <0-100> Set display brightness
  off                Turn display off (brightness 0)

Examples:
  sudo ./wigidash.py image photo.jpg
  sudo ./wigidash.py image photo.jpg --fit contain --bg 000000
  sudo ./wigidash.py monitor
  sudo ./wigidash.py monitor --interval 2
  sudo ./wigidash.py color ff0000
  sudo ./wigidash.py brightness 50

Requires: pyusb, Pillow, psutil, numpy
"""

import argparse
import os
import signal
import struct
import sys
import time

import numpy as np
import usb.core
import usb.util
from PIL import Image, ImageDraw, ImageFont

# ─── Device Constants ────────────────────────────────────────────────────────

VENDOR_ID = 0x28DA
PRODUCT_ID = 0xEF01
EP_OUT = 0x01

RT_WRITE = 0x21  # Class, Host-to-Device, Interface
RT_READ = 0xA1   # Class, Device-to-Host, Interface

CMD_CHECK_APP = 0x00
CMD_GET_FW_VERSION = 0x04
CMD_GET_CONFIG = 0x10
CMD_SET_CONFIG = 0x11
CMD_CLEAR_SCREEN_TIMEOUT = 0x12
CMD_GET_BRIGHTNESS = 0x50
CMD_SET_BRIGHTNESS = 0x51
CMD_WRITE_SETUP = 0x61
CMD_WRITE_DONE = 0x63
CMD_UI = 0x70
CMD_CLEAR_PAGE = 0x90
CMD_ADD_WIDGET = 0x91

WIDTH = 1024
HEIGHT = 592
FRAME_SIZE = WIDTH * HEIGHT * 2  # RGB565 = 2 bytes/pixel
TIMEOUT = 2000


# ─── Driver ──────────────────────────────────────────────────────────────────

class WigiDash:
    """Low-level driver for the G.Skill WigiDash USB display."""

    def __init__(self):
        self.dev = None
        self._initialized = False

    def connect(self):
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            return False
        if self.dev.is_kernel_driver_active(0):
            self.dev.detach_kernel_driver(0)
        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            pass
        try:
            usb.util.claim_interface(self.dev, 0)
        except usb.core.USBError:
            pass
        return True

    def close(self):
        if self.dev:
            try:
                usb.util.release_interface(self.dev, 0)
            except Exception:
                pass

    def _ctrl_write(self, bRequest, wValue=0, data=None):
        if data is None:
            data = b''
        self.dev.ctrl_transfer(RT_WRITE, bRequest, wValue, 0, data, TIMEOUT)

    def _ctrl_read(self, bRequest, wValue=0, length=1):
        return self.dev.ctrl_transfer(RT_READ, bRequest, wValue, 0, length, TIMEOUT).tobytes()

    def initialize(self):
        """Full initialization sequence matching the binary."""
        # Check app mode
        data = self._ctrl_read(CMD_CHECK_APP, 0, 3)
        if data[:2] != b'WD':
            raise RuntimeError("Device not in app mode")

        # Get config, clear timeout, set config back
        config = self._ctrl_read(CMD_GET_CONFIG, 0, 48)
        self._ctrl_write(CMD_CLEAR_SCREEN_TIMEOUT, 0)
        self._ctrl_write(CMD_SET_CONFIG, 0, config)

        # Clear page, switch to widget screen, add widget
        self._ctrl_write(CMD_CLEAR_PAGE, 0)
        self._ctrl_write(CMD_UI, 0x20)  # GoToScreen(1)
        widget_config = struct.pack('<HHHH', 0, 0, WIDTH, HEIGHT) + b'\x00' * 12
        self._ctrl_write(CMD_ADD_WIDGET, 0, widget_config)

        self._initialized = True

    def get_brightness(self):
        return self._ctrl_read(CMD_GET_BRIGHTNESS, 0, 1)[0]

    def set_brightness(self, level):
        level = max(0, min(100, level))
        self._ctrl_write(CMD_SET_BRIGHTNESS, 0, bytes([level]))

    def send_frame(self, rgb565_data):
        """Send a complete frame of RGB565 pixel data to the display."""
        if not self._initialized:
            self.initialize()

        # Pad or truncate
        if len(rgb565_data) < FRAME_SIZE:
            rgb565_data = rgb565_data + b'\x00' * (FRAME_SIZE - len(rgb565_data))
        elif len(rgb565_data) > FRAME_SIZE:
            rgb565_data = rgb565_data[:FRAME_SIZE]

        # Setup → Bulk → Done
        setup = struct.pack('<II', 0, FRAME_SIZE)
        self._ctrl_write(CMD_WRITE_SETUP, 0, setup)
        self.dev.write(EP_OUT, rgb565_data, timeout=5000)
        self._ctrl_write(CMD_WRITE_DONE, 0)

    def send_image(self, img):
        """Send a PIL Image (must be 1024x592 RGB) to the display."""
        self.send_frame(image_to_rgb565(img))


# ─── Image Conversion ────────────────────────────────────────────────────────

def image_to_rgb565(img):
    """Convert a PIL Image to RGB565 bytes (little-endian)."""
    if img.mode != 'RGB':
        img = img.convert('RGB')
    arr = np.array(img, dtype=np.uint16)
    r = (arr[:, :, 0] >> 3) << 11
    g = (arr[:, :, 1] >> 2) << 5
    b = arr[:, :, 2] >> 3
    rgb565 = (r | g | b).astype('<u2')
    return rgb565.tobytes()


def load_and_fit(path, fit='contain', bg_color=(0, 0, 0)):
    """Load an image and fit it to the display dimensions."""
    img = Image.open(path)
    if img.mode == 'RGBA':
        background = Image.new('RGB', img.size, bg_color)
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    if fit == 'stretch':
        return img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    elif fit == 'crop':
        # Center crop to fill
        ratio = max(WIDTH / img.width, HEIGHT / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - WIDTH) // 2
        top = (new_h - HEIGHT) // 2
        return img.crop((left, top, left + WIDTH, top + HEIGHT))
    else:  # contain
        canvas = Image.new('RGB', (WIDTH, HEIGHT), bg_color)
        ratio = min(WIDTH / img.width, HEIGHT / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        x = (WIDTH - new_w) // 2
        y = (HEIGHT - new_h) // 2
        canvas.paste(img, (x, y))
        return canvas


# ─── System Monitor ──────────────────────────────────────────────────────────

# Color palette (RGB tuples)
C_BG = (13, 17, 23)
C_TEXT = (230, 237, 243)
C_DIM = (125, 133, 144)
C_HEADER_BG = (22, 27, 34)
C_BORDER = (48, 54, 61)
C_BLUE = (88, 166, 255)
C_GREEN = (63, 185, 80)
C_YELLOW = (210, 153, 34)
C_RED = (248, 81, 73)
C_PURPLE = (139, 92, 246)
C_CYAN = (57, 211, 211)
C_ORANGE = (219, 148, 54)

FONT_PATHS = [
    '/usr/share/fonts/TTF/DejaVuSansMono.ttf',
    '/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
]

FONT_BOLD_PATHS = [
    '/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
]


def _find_font(paths, size):
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


class SystemMonitor:
    """Renders an htop-like system dashboard."""

    def __init__(self):
        import psutil
        self.psutil = psutil

        self.font_sm = _find_font(FONT_PATHS, 12)
        self.font_md = _find_font(FONT_PATHS, 14)
        self.font_lg = _find_font(FONT_BOLD_PATHS, 16)
        self.font_title = _find_font(FONT_BOLD_PATHS, 18)

        # State for delta calculations
        self._prev_net = None
        self._prev_disk = None
        self._prev_time = None
        self._prev_cpu_times = None

    def render(self):
        """Render a single frame of the system monitor dashboard."""
        psutil = self.psutil
        img = Image.new('RGB', (WIDTH, HEIGHT), C_BG)
        d = ImageDraw.Draw(img)

        y = 0

        # ── Header bar ───────────────────────────────────────────────
        y = self._draw_header(d, y)

        # ── CPU section ──────────────────────────────────────────────
        y = self._draw_cpu(d, y)

        # ── Memory section ───────────────────────────────────────────
        y = self._draw_memory(d, y)

        # ── Network & Disk ───────────────────────────────────────────
        y = self._draw_net_disk(d, y)

        # ── Temperatures ─────────────────────────────────────────────
        y = self._draw_temps(d, y)

        # ── Top processes ────────────────────────────────────────────
        self._draw_processes(d, y)

        return img

    def _draw_header(self, d, y):
        """Draw the header bar with hostname, uptime, and time."""
        bar_h = 28
        d.rectangle([0, y, WIDTH, y + bar_h], fill=C_HEADER_BG)

        hostname = os.uname().nodename
        d.text((10, y + 5), hostname, fill=C_BLUE, font=self.font_title)

        # Uptime
        uptime_s = time.time() - self.psutil.boot_time()
        days = int(uptime_s // 86400)
        hours = int((uptime_s % 86400) // 3600)
        mins = int((uptime_s % 3600) // 60)
        up_str = f"up {days}d {hours}h {mins}m" if days else f"up {hours}h {mins}m"
        d.text((WIDTH // 2 - 60, y + 7), up_str, fill=C_DIM, font=self.font_md)

        # Current time
        now = time.strftime("%H:%M:%S")
        d.text((WIDTH - 100, y + 7), now, fill=C_TEXT, font=self.font_md)

        return y + bar_h + 2

    def _bar_color(self, pct):
        """Return a color based on percentage (green → yellow → red)."""
        if pct < 50:
            t = pct / 50.0
            return (
                int(C_GREEN[0] + (C_YELLOW[0] - C_GREEN[0]) * t),
                int(C_GREEN[1] + (C_YELLOW[1] - C_GREEN[1]) * t),
                int(C_GREEN[2] + (C_YELLOW[2] - C_GREEN[2]) * t),
            )
        else:
            t = (pct - 50) / 50.0
            return (
                int(C_YELLOW[0] + (C_RED[0] - C_YELLOW[0]) * t),
                int(C_YELLOW[1] + (C_RED[1] - C_YELLOW[1]) * t),
                int(C_YELLOW[2] + (C_RED[2] - C_YELLOW[2]) * t),
            )

    def _draw_bar(self, d, x, y, w, h, pct, color=None, bg=C_BORDER):
        """Draw a horizontal progress bar."""
        pct = max(0, min(100, pct))
        d.rectangle([x, y, x + w, y + h], fill=bg)
        if pct > 0:
            fill_w = max(1, int(w * pct / 100))
            d.rectangle([x, y, x + fill_w, y + h], fill=color or self._bar_color(pct))

    def _draw_cpu(self, d, y):
        """Draw CPU usage with per-core bars."""
        psutil = self.psutil

        d.text((10, y + 1), "CPU", fill=C_BLUE, font=self.font_sm)

        # Overall CPU
        cpu_pct = psutil.cpu_percent(interval=0)
        self._draw_bar(d, 48, y + 2, 200, 12, cpu_pct)
        d.text((255, y + 1), f"{cpu_pct:5.1f}%", fill=C_TEXT, font=self.font_sm)

        # Load average
        load = os.getloadavg()
        d.text((330, y + 1), f"Load: {load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}",
               fill=C_DIM, font=self.font_sm)

        y += 18

        # Per-core bars (2 columns)
        per_cpu = psutil.cpu_percent(interval=0, percpu=True)
        ncores = len(per_cpu)
        rows = (ncores + 1) // 2
        col_w = 490

        for i, pct in enumerate(per_cpu):
            col = i // rows
            row = i % rows
            bx = 10 + col * col_w
            by = y + row * 18

            label = f"{i:2d}"
            d.text((bx, by + 1), label, fill=C_DIM, font=self.font_sm)
            self._draw_bar(d, bx + 28, by + 2, 200, 12, pct)
            d.text((bx + 235, by + 1), f"{pct:3.0f}%", fill=C_TEXT, font=self.font_sm)

        y += rows * 18 + 2
        return y

    def _draw_memory(self, d, y):
        """Draw memory and swap usage on a single line."""
        psutil = self.psutil

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        def fmt_gb(b):
            gb = b / (1024 ** 3)
            return f"{gb:.1f}G" if gb >= 1 else f"{b / (1024 ** 2):.0f}M"

        # RAM (left half)
        d.text((10, y + 1), "MEM", fill=C_PURPLE, font=self.font_sm)
        self._draw_bar(d, 48, y + 2, 200, 12, mem.percent, color=C_PURPLE)
        d.text((255, y + 1),
               f"{fmt_gb(mem.used)}/{fmt_gb(mem.total)}",
               fill=C_TEXT, font=self.font_sm)

        # Swap (right half, aligned with CPU right column at x=500)
        rx = 500
        d.text((rx, y + 1), "SWP", fill=C_PURPLE, font=self.font_sm)
        swap_pct = swap.percent if swap.total > 0 else 0
        self._draw_bar(d, rx + 38, y + 2, 200, 12, swap_pct, color=C_PURPLE)
        if swap.total > 0:
            d.text((rx + 245, y + 1),
                   f"{fmt_gb(swap.used)}/{fmt_gb(swap.total)}",
                   fill=C_TEXT, font=self.font_sm)
        else:
            d.text((rx + 245, y + 1), "N/A", fill=C_DIM, font=self.font_sm)

        y += 18
        return y + 2

    def _draw_net_disk(self, d, y):
        """Draw network and disk I/O rates."""
        psutil = self.psutil
        now = time.time()

        net = psutil.net_io_counters()
        disk = psutil.disk_io_counters()

        if self._prev_net and self._prev_time:
            dt = now - self._prev_time
            if dt > 0:
                net_up = (net.bytes_sent - self._prev_net.bytes_sent) / dt
                net_dn = (net.bytes_recv - self._prev_net.bytes_recv) / dt
                disk_r = (disk.read_bytes - self._prev_disk.read_bytes) / dt
                disk_w = (disk.write_bytes - self._prev_disk.write_bytes) / dt
            else:
                net_up = net_dn = disk_r = disk_w = 0
        else:
            net_up = net_dn = disk_r = disk_w = 0

        self._prev_net = net
        self._prev_disk = disk
        self._prev_time = now

        def fmt_rate(bps):
            if bps >= 1e9:
                return f"{bps / 1e9:.1f} GB/s"
            elif bps >= 1e6:
                return f"{bps / 1e6:.1f} MB/s"
            elif bps >= 1e3:
                return f"{bps / 1e3:.1f} KB/s"
            return f"{bps:.0f} B/s"

        # Network
        d.text((10, y + 1), "NET", fill=C_CYAN, font=self.font_sm)
        d.text((48, y + 1), f"\u2191 {fmt_rate(net_up)}", fill=C_GREEN, font=self.font_sm)
        d.text((195, y + 1), f"\u2193 {fmt_rate(net_dn)}", fill=C_BLUE, font=self.font_sm)

        # Disk (aligned with right column at x=500)
        d.text((500, y + 1), "DSK", fill=C_ORANGE, font=self.font_sm)
        d.text((538, y + 1), f"R {fmt_rate(disk_r)}", fill=C_GREEN, font=self.font_sm)
        d.text((685, y + 1), f"W {fmt_rate(disk_w)}", fill=C_RED, font=self.font_sm)

        y += 18
        return y + 2

    def _categorize_temps(self, temps):
        """Extract key temperature readings with clean labels.

        Focuses on: CPU, GPU, NVMe drives, coolant, and PSU.
        """
        readings = []

        # CPU: k10temp (AMD), coretemp (Intel), cpu_thermal (ARM)
        for dev in ('k10temp', 'coretemp', 'cpu_thermal', 'zenpower'):
            if dev not in temps:
                continue
            entries = temps[dev]
            # Prefer Tctl/Package, fall back to max
            pkg = [e for e in entries if e.label in ('Tctl', 'Package id 0', 'Tdie')]
            if pkg:
                readings.append(('CPU', pkg[0].current))
            elif entries:
                readings.append(('CPU', max(e.current for e in entries)))
            # Per-CCD temps (AMD)
            for e in entries:
                if e.label.startswith('Tccd'):
                    readings.append((e.label, e.current))
            break

        # GPU: amdgpu, nvidia, nouveau
        for dev in ('amdgpu', 'nvidia', 'nouveau'):
            if dev not in temps:
                continue
            for e in temps[dev]:
                label = e.label or 'GPU'
                if label == 'edge':
                    label = 'GPU'
                elif label == 'junction':
                    label = 'GPU Hot'
                elif label == 'mem':
                    label = 'GPU Mem'
                readings.append((label, e.current))
            break

        # NVMe: number each drive by its Composite entries
        if 'nvme' in temps:
            drive_num = 0
            for e in temps['nvme']:
                if e.label == 'Composite':
                    drive_num += 1
                    readings.append((f'NVMe {drive_num}', e.current))

        # Coolant: d5next, liquidctl, or anything with "coolant"/"liquid" in label
        for dev, entries in temps.items():
            for e in entries:
                low = (e.label or '').lower()
                if 'coolant' in low or 'liquid' in low:
                    readings.append(('Coolant', e.current))

        # PSU (case temp only)
        if 'corsairpsu' in temps:
            for e in temps['corsairpsu']:
                if 'case' in (e.label or '').lower():
                    readings.append(('PSU', e.current))
                    break

        return readings

    def _draw_temps(self, d, y):
        """Draw key temperature readings in a compact row (Fahrenheit)."""
        try:
            temps = self.psutil.sensors_temperatures()
        except (AttributeError, RuntimeError):
            temps = {}

        if temps:
            readings = self._categorize_temps(temps)
            if readings:
                # Cache successful readings so flaky sensors don't flicker
                self._cached_temps = readings

        readings = getattr(self, '_cached_temps', None)
        if not readings:
            return y

        # Compact grid (Fahrenheit, thresholds adjusted: 140/176F = 60/80C)
        col_w = 120
        cols = WIDTH // col_w
        for i, (label, temp_c) in enumerate(readings):
            col = i % cols
            row = i // cols
            x = 10 + col * col_w
            ty = y + row * 16
            temp_f = temp_c * 9 / 5 + 32
            color = C_GREEN if temp_c < 60 else (C_YELLOW if temp_c < 80 else C_RED)
            d.text((x, ty), f"{label} {temp_f:.0f}F", fill=color, font=self.font_sm)

        num_rows = (len(readings) + cols - 1) // cols
        y += num_rows * 16 + 2
        return y

    def _draw_processes(self, d, y):
        """Draw top processes by CPU usage."""
        psutil = self.psutil

        d.text((10, y), "PROCESSES", fill=C_DIM, font=self.font_sm)
        y += 16

        # Column headers
        headers = f"{'PID':>7}  {'CPU%':>5}  {'MEM%':>5}  {'USER':<10}  {'COMMAND':<50}"
        d.text((10, y), headers, fill=C_DIM, font=self.font_sm)
        y += 16

        # Get processes sorted by CPU
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'username']):
            try:
                info = p.info
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda p: p.get('cpu_percent', 0) or 0, reverse=True)

        # Calculate how many rows fit
        max_rows = (HEIGHT - y - 4) // 15
        for proc in procs[:max_rows]:
            pid = proc.get('pid', 0)
            cpu = proc.get('cpu_percent', 0) or 0
            mem = proc.get('memory_percent', 0) or 0
            user = (proc.get('username') or '?')[:10]
            name = (proc.get('name') or '?')[:50]

            color = C_TEXT if cpu < 50 else (C_YELLOW if cpu < 90 else C_RED)
            line = f"{pid:>7}  {cpu:>5.1f}  {mem:>5.1f}  {user:<10}  {name:<50}"
            d.text((10, y), line, fill=color, font=self.font_sm)
            y += 15

        return y


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_image(args):
    """Display an image file on the WigiDash."""
    if not os.path.isfile(args.file):
        print(f"Error: file not found: {args.file}")
        sys.exit(1)

    bg = tuple(int(args.bg[i:i+2], 16) for i in (0, 2, 4)) if args.bg else (0, 0, 0)
    img = load_and_fit(args.file, fit=args.fit, bg_color=bg)

    wigi = WigiDash()
    if not wigi.connect():
        print("Error: WigiDash not found!")
        sys.exit(1)

    try:
        print(f"Displaying: {args.file} (fit={args.fit})")
        wigi.send_image(img)
        print("Done.")
    finally:
        wigi.close()


def cmd_color(args):
    """Fill the display with a solid color."""
    try:
        r = int(args.hex_color[0:2], 16)
        g = int(args.hex_color[2:4], 16)
        b = int(args.hex_color[4:6], 16)
    except (ValueError, IndexError):
        print("Error: invalid color. Use 6-digit hex like ff0000")
        sys.exit(1)

    # Convert to RGB565
    pixel = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    frame = struct.pack('<H', pixel) * (WIDTH * HEIGHT)

    wigi = WigiDash()
    if not wigi.connect():
        print("Error: WigiDash not found!")
        sys.exit(1)

    try:
        print(f"Filling display with #{args.hex_color}")
        wigi.send_frame(frame)
        print("Done.")
    finally:
        wigi.close()


def cmd_brightness(args):
    """Set display brightness."""
    wigi = WigiDash()
    if not wigi.connect():
        print("Error: WigiDash not found!")
        sys.exit(1)

    try:
        wigi.set_brightness(args.level)
        print(f"Brightness set to {args.level}")
    finally:
        wigi.close()


def cmd_off(args):
    """Turn display off (brightness 0)."""
    wigi = WigiDash()
    if not wigi.connect():
        print("Error: WigiDash not found!")
        sys.exit(1)

    try:
        wigi.set_brightness(0)
        print("Display off.")
    finally:
        wigi.close()


def cmd_monitor(args):
    """Run the live system monitor dashboard."""
    wigi = WigiDash()
    if not wigi.connect():
        print("Error: WigiDash not found!")
        sys.exit(1)

    monitor = SystemMonitor()
    interval = args.interval
    running = True

    def sigint_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, sigint_handler)

    try:
        # First render takes a moment for CPU stats to settle
        print(f"System monitor running (interval={interval}s). Press Ctrl+C to stop.")
        frame_count = 0
        while running:
            t0 = time.time()
            img = monitor.render()
            t_render = time.time() - t0

            t1 = time.time()
            wigi.send_image(img)
            t_send = time.time() - t1

            frame_count += 1
            fps_info = f"render={t_render*1000:.0f}ms send={t_send*1000:.0f}ms"
            print(f"\r  Frame {frame_count}: {fps_info}     ", end='', flush=True)

            # Sleep for the remainder of the interval
            elapsed = time.time() - t0
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0 and running:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopped.")
        wigi.close()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='wigidash',
        description='Display utility for the G.Skill WigiDash USB display.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  sudo ./wigidash.py image photo.jpg\n"
            "  sudo ./wigidash.py image photo.jpg --fit crop\n"
            "  sudo ./wigidash.py monitor\n"
            "  sudo ./wigidash.py monitor --interval 0.5\n"
            "  sudo ./wigidash.py color ff0000\n"
            "  sudo ./wigidash.py brightness 75\n"
        ),
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # image
    p_img = sub.add_parser('image', help='Display an image file')
    p_img.add_argument('file', help='Path to image file (PNG, JPEG, BMP, etc.)')
    p_img.add_argument('--fit', choices=['contain', 'crop', 'stretch'], default='contain',
                       help='How to fit the image (default: contain)')
    p_img.add_argument('--bg', default='000000', metavar='RRGGBB',
                       help='Background color for contain mode (default: 000000)')
    p_img.set_defaults(func=cmd_image)

    # monitor
    p_mon = sub.add_parser('monitor', help='Live system monitor dashboard')
    p_mon.add_argument('--interval', type=float, default=1.0,
                       help='Update interval in seconds (default: 1.0)')
    p_mon.set_defaults(func=cmd_monitor)

    # color
    p_col = sub.add_parser('color', help='Fill display with a solid color')
    p_col.add_argument('hex_color', metavar='RRGGBB', help='6-digit hex color (e.g. ff0000)')
    p_col.set_defaults(func=cmd_color)

    # brightness
    p_bri = sub.add_parser('brightness', help='Set display brightness')
    p_bri.add_argument('level', type=int, help='Brightness level (0-100)')
    p_bri.set_defaults(func=cmd_brightness)

    # off
    p_off = sub.add_parser('off', help='Turn display off (brightness 0)')
    p_off.set_defaults(func=cmd_off)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
