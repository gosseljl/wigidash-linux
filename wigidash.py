#!/usr/bin/env python3
"""
WigiDash - Display utility for the G.Skill WigiDash USB display.

Commands:
  image <file>       Display an image (PNG, JPEG, BMP, etc.)
  monitor            Live system monitor (graphical dashboard)
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
Optional: dbus-python + PyGObject (sleep/wake detection)
"""

import argparse
import ctypes
import os
import signal
import struct
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

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

    def reconnect(self):
        """Release old handle and reconnect to the device."""
        self.close()
        self._initialized = False
        # Device may take a moment to re-enumerate after wake
        for attempt in range(10):
            if self.connect():
                self.initialize()
                return True
            time.sleep(0.5)
        return False

    def close(self):
        if self.dev:
            try:
                usb.util.release_interface(self.dev, 0)
            except Exception:
                pass
            self.dev = None
            self._initialized = False

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

# Color palette — dark/orange theme
C_BG = (20, 20, 36)
C_PANEL = (28, 28, 48)
C_BORDER = (55, 55, 75)
C_TEXT = (230, 237, 243)
C_DIM = (125, 133, 144)
C_ACCENT = (219, 148, 54)
C_GREEN = (63, 185, 80)
C_YELLOW = (210, 153, 34)
C_RED = (248, 81, 73)
C_BLUE = (88, 166, 255)
C_PURPLE = (139, 92, 246)
C_CYAN = (57, 211, 211)

# Layout — 3-column grid
MARGIN = 9
GAP = 8
PANEL_W = 330
HEADER_H = 36
ROW_H = 270
COL_X = [MARGIN, MARGIN + PANEL_W + GAP, MARGIN + 2 * (PANEL_W + GAP)]
ROW_Y = [HEADER_H + GAP, HEADER_H + GAP + ROW_H + GAP]

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
    """Renders a graphical system dashboard with panels, arc gauges, and sparklines."""

    def __init__(self):
        import psutil
        self.psutil = psutil

        # Fonts — sized for readability on the 5.5" 1024x592 display
        self.font_sm = _find_font(FONT_PATHS, 16)
        self.font_md = _find_font(FONT_PATHS, 18)
        self.font_stat = _find_font(FONT_PATHS, 18)
        self.font_label = _find_font(FONT_BOLD_PATHS, 20)
        self.font_title = _find_font(FONT_BOLD_PATHS, 22)
        self.font_arc = _find_font(FONT_BOLD_PATHS, 40)

        # ── Fast data (updated every frame in render thread) ──
        self._net_up_history = deque(maxlen=60)
        self._net_dn_history = deque(maxlen=60)
        self._prev_net = None
        self._prev_time = None
        self._net_up_rate = 0
        self._net_dn_rate = 0

        # ── Slow data (updated by background collector thread) ──
        self._cpu_pct = 0
        self._cpu_freq = None
        self._cpu_power_w = 0
        self._gpu_stats = None
        self._has_gpu = False
        self._psu = {}
        self._cached_temps = None
        self._top_procs = []
        self._mem = psutil.virtual_memory()
        self._swap = psutil.swap_memory()
        self._partitions = []  # [(mount, usage), ...]

        # ── RAPL setup ──
        self._rapl_path = None
        self._rapl_prev_energy = None
        self._rapl_prev_time = None
        rapl = '/sys/class/powercap/intel-rapl:0/energy_uj'
        if os.path.exists(rapl):
            self._rapl_path = rapl

        # ── PSU sensor paths (tiered: fast=power, slow=volts/temps/fan) ──
        self._psu_fast = {}   # power rails — read every cycle
        self._psu_slow = {}   # volts, temps, fan — read every other cycle
        try:
            for hwmon in sorted(os.listdir('/sys/class/hwmon/')):
                name_path = f'/sys/class/hwmon/{hwmon}/name'
                if os.path.exists(name_path):
                    with open(name_path) as f:
                        if f.read().strip() == 'corsairpsu':
                            base = f'/sys/class/hwmon/{hwmon}'
                            fast = {
                                'power_total': 'power1_input',
                                'power_12v':   'power2_input',
                                'power_5v':    'power3_input',
                                'power_3v3':   'power4_input',
                            }
                            slow = {
                                'volt_12v':  'in1_input',
                                'volt_5v':   'in2_input',
                                'volt_3v3':  'in3_input',
                                'temp_vrm':  'temp1_input',
                                'temp_case': 'temp2_input',
                                'fan_rpm':   'fan1_input',
                            }
                            for key, fname in fast.items():
                                p = f'{base}/{fname}'
                                if os.path.exists(p):
                                    self._psu_fast[key] = p
                            for key, fname in slow.items():
                                p = f'{base}/{fname}'
                                if os.path.exists(p):
                                    self._psu_slow[key] = p
                            break
        except Exception:
            pass

        # ── GPU via NVML ctypes (fast, no subprocess) ──
        self._nvml = None
        self._nvml_handle = None
        try:
            nvml = ctypes.CDLL('libnvidia-ml.so.1')
            if nvml.nvmlInit_v2() == 0:
                handle = ctypes.c_void_p()
                if nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) == 0:
                    self._nvml = nvml
                    self._nvml_handle = handle
                    self._has_gpu = True
        except Exception:
            pass

        # Prime CPU percent
        psutil.cpu_percent(interval=0)

        # Pre-populate disk partitions
        self._update_partitions()

        # ── Thread pool for parallel I/O ──
        self._pool = ThreadPoolExecutor(max_workers=3)

        # ── Start background data collector ──
        self._collector_stop = threading.Event()
        self._collector = threading.Thread(target=self._collect_loop, daemon=True)
        self._collector.start()

    def stop(self):
        """Stop the background collector thread."""
        self._collector_stop.set()
        self._pool.shutdown(wait=False)
        if self._nvml:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass

    # ── Background data collection ───────────────────────────────────

    def _collect_loop(self):
        """Background thread: collects sensor data with parallel I/O."""
        psutil = self.psutil
        cycle = 0
        while not self._collector_stop.is_set():
            try:
                self._collect_cycle(psutil, cycle)
                cycle += 1
            except Exception:
                pass
            self._collector_stop.wait(1.0)

    def _collect_cycle(self, psutil, cycle):
        """Single collection cycle — overlaps PSU I/O with other work."""
        pool = self._pool

        # ── Launch slow I/O in parallel threads ──
        psu_future = pool.submit(self._read_psu_sensors, cycle)
        if cycle % 2 == 0:
            temps_future = pool.submit(psutil.sensors_temperatures)
            procs_future = pool.submit(self._scan_top_procs, psutil)
        else:
            temps_future = procs_future = None

        # ── Fast inline work (while PSU I/O is in flight) ──
        self._cpu_pct = psutil.cpu_percent(interval=0)
        self._cpu_freq = psutil.cpu_freq()
        self._mem = psutil.virtual_memory()
        self._swap = psutil.swap_memory()

        if self._nvml and self._nvml_handle:
            self._gpu_stats = self._query_gpu_nvml()

        if self._rapl_path:
            try:
                now = time.time()
                with open(self._rapl_path) as f:
                    energy_uj = int(f.read().strip())
                if self._rapl_prev_energy is not None:
                    dt = now - self._rapl_prev_time
                    if dt > 0:
                        delta = energy_uj - self._rapl_prev_energy
                        if delta >= 0:
                            self._cpu_power_w = delta / (dt * 1e6)
                self._rapl_prev_energy = energy_uj
                self._rapl_prev_time = now
            except Exception:
                pass

        # ── Collect parallel results ──
        psu = psu_future.result()
        if psu:
            self._psu.update(psu)

        if temps_future:
            try:
                temps = temps_future.result()
                if temps:
                    readings = self._categorize_temps(temps)
                    if readings:
                        self._cached_temps = readings
            except Exception:
                pass

        if procs_future:
            try:
                self._top_procs = procs_future.result()
            except Exception:
                pass

        # Very slow: partitions every 10 cycles
        if cycle % 10 == 0:
            self._update_partitions()

    def _read_psu_sensors(self, cycle):
        """Read PSU sysfs — power every cycle, volts/temps every other."""
        psu = {}
        for key, path in self._psu_fast.items():
            try:
                with open(path) as f:
                    psu[key] = int(f.read().strip()) / 1_000_000
            except Exception:
                pass
        if cycle % 2 == 0:
            for key, path in self._psu_slow.items():
                try:
                    raw = int(open(path).read().strip())
                    if key.startswith('volt_'):
                        psu[key] = raw / 1_000
                    elif key.startswith('temp_'):
                        psu[key] = raw / 1_000
                    elif key == 'fan_rpm':
                        psu[key] = raw
                except Exception:
                    pass
        return psu

    @staticmethod
    def _scan_top_procs(psutil):
        """Scan for top CPU-consuming processes."""
        procs = []
        for p in psutil.process_iter(['name', 'cpu_percent']):
            info = p.info
            cpu = info.get('cpu_percent') or 0
            name = info.get('name') or '?'
            if cpu > 0 and name not in ('System Idle Process', 'idle'):
                procs.append((cpu, name))
        procs.sort(key=lambda x: x[0], reverse=True)
        return procs[:3]

    def _update_partitions(self):
        """Refresh disk partition list (very slow, run infrequently)."""
        psutil = self.psutil
        real_fs = {'ext4', 'btrfs', 'xfs', 'ntfs', 'vfat', 'ext3', 'zfs', 'f2fs'}
        filtered = []
        try:
            for p in psutil.disk_partitions():
                if p.fstype not in real_fs:
                    continue
                if '/boot' in p.mountpoint or '/snap' in p.mountpoint:
                    continue
                try:
                    usage = psutil.disk_usage(p.mountpoint)
                    filtered.append((p.mountpoint, usage))
                except (PermissionError, OSError):
                    continue
        except Exception:
            pass
        self._partitions = filtered[:5]

    # ── Fast render-time updates (network rates only) ────────────────

    def _update_rates(self):
        """Update network rates — the only thing that must run in the render thread."""
        psutil = self.psutil
        now = time.time()
        net = psutil.net_io_counters()

        if self._prev_net and self._prev_time:
            dt = now - self._prev_time
            if dt > 0:
                self._net_up_rate = (net.bytes_sent - self._prev_net.bytes_sent) / dt
                self._net_dn_rate = (net.bytes_recv - self._prev_net.bytes_recv) / dt

        self._prev_net = net
        self._prev_time = now

        self._net_up_history.append(self._net_up_rate)
        self._net_dn_history.append(self._net_dn_rate)

    def _query_gpu_nvml(self):
        """Query GPU stats via NVML ctypes — ~0.05ms per call."""
        nvml = self._nvml
        h = self._nvml_handle
        try:
            class _Util(ctypes.Structure):
                _fields_ = [('gpu', ctypes.c_uint), ('memory', ctypes.c_uint)]
            class _Mem(ctypes.Structure):
                _fields_ = [('total', ctypes.c_ulonglong),
                             ('free', ctypes.c_ulonglong),
                             ('used', ctypes.c_ulonglong)]

            u = _Util()
            nvml.nvmlDeviceGetUtilizationRates(h, ctypes.byref(u))

            m = _Mem()
            nvml.nvmlDeviceGetMemoryInfo(h, ctypes.byref(m))

            temp = ctypes.c_uint()
            nvml.nvmlDeviceGetTemperature(h, 0, ctypes.byref(temp))

            power = ctypes.c_uint()
            nvml.nvmlDeviceGetPowerUsage(h, ctypes.byref(power))

            clock = ctypes.c_uint()
            nvml.nvmlDeviceGetClockInfo(h, 0, ctypes.byref(clock))

            return {
                'util': u.gpu,
                'clock': clock.value,
                'vram_used': m.used / (1024 ** 3),
                'vram_total': m.total / (1024 ** 3),
                'power': power.value / 1000,
                'temp_c': temp.value,
            }
        except Exception:
            return None

    # ── Drawing primitives ──────────────────────────────────────────

    def _bar_color(self, pct):
        """Return a color based on percentage (green -> yellow -> red)."""
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

    @staticmethod
    def _fmt_rate(bps):
        """Format bytes/sec to human-readable string."""
        bps = max(0, bps)
        if bps >= 1e9:
            return f"{bps / 1e9:.1f} GB/s"
        if bps >= 1e6:
            return f"{bps / 1e6:.1f} MB/s"
        if bps >= 1e3:
            return f"{bps / 1e3:.1f} KB/s"
        return f"{bps:.0f} B/s"

    def _draw_panel(self, d, x, y, w=PANEL_W, h=ROW_H, label=None):
        """Draw a rounded-rectangle panel with an orange section label."""
        d.rounded_rectangle([x, y, x + w, y + h], radius=8,
                            fill=C_PANEL, outline=C_BORDER)
        if label:
            d.text((x + 12, y + 8), label, fill=C_ACCENT, font=self.font_label)

    def _draw_arc_gauge(self, d, cx, cy, diameter, pct, label=None):
        """Draw a 270-degree arc gauge with percentage text centered inside."""
        r = diameter // 2
        stroke = 14
        bbox = [cx - r, cy - r, cx + r, cy + r]

        # Background track (270 deg, open at bottom)
        d.arc(bbox, 135, 45, fill=C_BORDER, width=stroke)

        # Filled arc
        pct_clamped = max(0, min(100, pct))
        if pct_clamped > 0:
            end_angle = 135 + 270 * pct_clamped / 100
            d.arc(bbox, 135, end_angle,
                  fill=self._bar_color(pct_clamped), width=stroke)

        # Big percentage number centered in the arc
        d.text((cx, cy - 8), f"{pct:.0f}%",
               fill=C_TEXT, font=self.font_arc, anchor="mm")

        # Small label below the number
        if label:
            d.text((cx, cy + 26), label,
                   fill=C_DIM, font=self.font_stat, anchor="mm")

    def _draw_progress_bar(self, d, x, y, w, h, pct):
        """Draw a rounded horizontal progress bar."""
        pct = max(0, min(100, pct))
        radius = h // 3
        d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=C_BORDER)
        if pct > 0:
            fill_w = max(h, int(w * pct / 100))
            d.rounded_rectangle([x, y, x + fill_w, y + h], radius=radius,
                                fill=self._bar_color(pct))

    def _draw_sparkline(self, d, x, y, w, h, data, line_color, fill_color):
        """Draw a mini area chart with filled polygon and line on top."""
        # Background
        d.rounded_rectangle([x, y, x + w, y + h], radius=3, fill=(25, 25, 42))

        if len(data) < 2:
            return

        max_val = max(data)
        if max_val <= 0:
            return

        n = len(data)
        points = []
        for i, val in enumerate(data):
            px = x + int(i * w / max(n - 1, 1))
            py_pt = y + h - 2 - int((val / max_val) * (h - 4))
            py_pt = max(y + 1, min(y + h - 2, py_pt))
            points.append((px, py_pt))

        # Filled area under the line
        fill_pts = [(x, y + h - 1)] + points + [(x + w, y + h - 1)]
        d.polygon(fill_pts, fill=fill_color)

        # Line on top
        d.line(points, fill=line_color, width=2)

    def _draw_stat_row(self, d, x, y, w, label, value, color=C_TEXT):
        """Draw a label on the left and a right-aligned value."""
        d.text((x, y), label, fill=C_DIM, font=self.font_stat)
        d.text((x + w, y), value, fill=color, font=self.font_stat, anchor="ra")

    # ── Data extraction ─────────────────────────────────────────────

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

        # PSU temps — skip if corsairpsu detail is shown in the Power section
        # (avoids duplication since the Memory panel shows VRM + case temps)

        return readings

    # ── Panel renderers ─────────────────────────────────────────────

    def _draw_header(self, d):
        """Full-width header bar with hostname, uptime, and clock."""
        d.rectangle([0, 0, WIDTH - 1, HEADER_H], fill=C_PANEL)

        # Hostname (left)
        hostname = os.uname().nodename
        d.text((12, 8), hostname, fill=C_ACCENT, font=self.font_title)

        # Uptime (center)
        uptime_s = time.time() - self.psutil.boot_time()
        days = int(uptime_s // 86400)
        hours = int((uptime_s % 86400) // 3600)
        mins = int((uptime_s % 3600) // 60)
        up_str = f"up {days}d {hours}h {mins}m" if days else f"up {hours}h {mins}m"
        d.text((WIDTH // 2, HEADER_H // 2), up_str,
               fill=C_DIM, font=self.font_md, anchor="mm")

        # Clock (right)
        now = time.strftime("%H:%M:%S")
        d.text((WIDTH - 12, 8), now,
               fill=C_TEXT, font=self.font_title, anchor="ra")

    def _draw_cpu_panel(self, d, px, py):
        """CPU panel: arc gauge + stats (all data from background thread)."""
        self._draw_panel(d, px, py, label="CPU")

        sx = px + 16
        sw = PANEL_W - 32

        cx = px + PANEL_W // 2
        cy = py + 34 + 70
        self._draw_arc_gauge(d, cx, cy, 140, self._cpu_pct, label="Load")

        sy = cy + 60
        d.line([(sx, sy), (sx + sw, sy)], fill=C_BORDER, width=1)
        sy += 8

        freq = self._cpu_freq
        if freq:
            self._draw_stat_row(d, sx, sy, sw, "Clock",
                                f"{freq.current:.0f} MHz")

        # CPU temp from cached temps
        temp_str = "N/A"
        if self._cached_temps:
            for label, tc in self._cached_temps:
                if label == 'CPU':
                    temp_str = f"{tc * 9 / 5 + 32:.0f}\u00b0F"
                    break
        self._draw_stat_row(d, sx, sy + 22, sw, "Temp", temp_str)

        load = os.getloadavg()
        self._draw_stat_row(d, sx, sy + 44, sw, "Load Avg", f"{load[0]:.2f}")

        ncores = self.psutil.cpu_count(logical=True)
        self._draw_stat_row(d, sx, sy + 66, sw, "Cores", str(ncores))

    def _draw_gpu_panel(self, d, px, py):
        """GPU panel: arc gauge + stats via nvidia-smi, or N/A fallback."""
        self._draw_panel(d, px, py, label="GPU")

        cx = px + PANEL_W // 2
        cy = py + 34 + 70
        sx = px + 16
        sw = PANEL_W - 32

        gpu = self._gpu_stats

        if not self._has_gpu or gpu is None:
            self._draw_arc_gauge(d, cx, cy, 140, 0, label="N/A")
            sy = cy + 60
            d.line([(sx, sy), (sx + sw, sy)], fill=C_BORDER, width=1)
            d.text((sx, sy + 12), "No GPU detected",
                   fill=C_DIM, font=self.font_stat)
            return

        self._draw_arc_gauge(d, cx, cy, 140, gpu['util'], label="Load")

        # Separator
        sy = cy + 60
        d.line([(sx, sy), (sx + sw, sy)], fill=C_BORDER, width=1)
        sy += 8

        self._draw_stat_row(d, sx, sy, sw, "Clock",
                            f"{gpu['clock']} MHz")
        self._draw_stat_row(d, sx, sy + 22, sw, "VRAM",
                            f"{gpu['vram_used']:.1f}/{gpu['vram_total']:.0f} GB")
        self._draw_stat_row(d, sx, sy + 44, sw, "Power",
                            f"{gpu['power']:.0f} W")
        tf = gpu['temp_c'] * 9 / 5 + 32
        self._draw_stat_row(d, sx, sy + 66, sw, "Temp",
                            f"{tf:.0f}\u00b0F")

    def _draw_temps_panel(self, d, px, py):
        """Temperatures panel: list of sensor readings (from background thread)."""
        self._draw_panel(d, px, py, label="Temperatures")

        readings = self._cached_temps
        if not readings:
            d.text((px + 16, py + 36), "No sensors",
                   fill=C_DIM, font=self.font_stat)
            return

        sx = px + 16
        sw = PANEL_W - 32
        sy = py + 34
        line_h = 28
        max_items = (ROW_H - 42) // line_h

        for i, (label, temp_c) in enumerate(readings[:max_items]):
            ty = sy + i * line_h
            temp_f = temp_c * 9 / 5 + 32
            color = (C_GREEN if temp_c < 60
                     else (C_YELLOW if temp_c < 80 else C_RED))

            # Colored indicator dot
            dot_y = ty + 6
            d.ellipse([sx, dot_y, sx + 10, dot_y + 10], fill=color)

            # Label and value
            d.text((sx + 16, ty), label, fill=C_TEXT, font=self.font_stat)
            d.text((sx + sw, ty), f"{temp_f:.0f}\u00b0F",
                   fill=color, font=self.font_stat, anchor="ra")

    def _draw_memory_panel(self, d, px, py):
        """Memory panel: RAM bar, Swap bar, Disk I/O rates."""
        self._draw_panel(d, px, py, label="Memory")

        mem = self._mem
        swap = self._swap

        sx = px + 16
        sw = PANEL_W - 32
        sy = py + 34

        def fmt_gb(b):
            gb = b / (1024 ** 3)
            return f"{gb:.1f}" if gb >= 1 else f"{b / (1024 ** 2):.0f}M"

        # ── RAM ──
        d.text((sx, sy), "RAM", fill=C_DIM, font=self.font_stat)
        d.text((sx + sw, sy), f"{mem.percent:.1f}%",
               fill=C_TEXT, font=self.font_stat, anchor="ra")
        sy += 22
        self._draw_progress_bar(d, sx, sy, sw, 16, mem.percent)
        sy += 20
        d.text((sx, sy), f"{fmt_gb(mem.used)} / {fmt_gb(mem.total)} GB",
               fill=C_DIM, font=self.font_sm)

        # ── Swap (compact) ──
        if swap.total > 0:
            sy += 24
            d.text((sx, sy), "Swap", fill=C_DIM, font=self.font_stat)
            d.text((sx + sw, sy), f"{swap.percent:.1f}% — "
                   f"{fmt_gb(swap.used)}/{fmt_gb(swap.total)} GB",
                   fill=C_TEXT, font=self.font_stat, anchor="ra")

        # ── Power ──
        sy += 26
        psu = self._psu
        total_w = psu.get('power_total', 0)
        cpu_w = self._cpu_power_w
        gpu_w = self._gpu_stats['power'] if self._gpu_stats else 0

        # Header line: "Power" left, total watts right
        d.text((sx, sy), "Power", fill=C_DIM, font=self.font_stat)
        if total_w > 0:
            d.text((sx + sw, sy), f"{total_w:.0f}W",
                   fill=C_ACCENT, font=self.font_stat, anchor="ra")
        sy += 22

        # CPU / GPU power
        col_mid = sx + sw // 2
        if cpu_w > 0:
            d.text((sx, sy), f"CPU  {cpu_w:.0f}W", fill=C_TEXT, font=self.font_stat)
        if gpu_w > 0:
            d.text((col_mid, sy), f"GPU  {gpu_w:.0f}W", fill=C_TEXT, font=self.font_stat)
        sy += 24

        # Per-rail breakdown with aligned columns
        # Col layout: label(50px) | watts right-aligned(100px) | voltage right-aligned(end)
        watt_x = sx + 110  # right edge for watt column
        rails = [
            ('+12V', psu.get('power_12v', 0), psu.get('volt_12v', 0)),
            ('+5V',  psu.get('power_5v', 0),  psu.get('volt_5v', 0)),
            ('+3.3V', psu.get('power_3v3', 0), psu.get('volt_3v3', 0)),
        ]
        for label, watts, volts in rails:
            d.text((sx, sy), label, fill=C_DIM, font=self.font_sm)
            if watts > 0:
                d.text((watt_x, sy), f"{watts:.0f}W",
                       fill=C_TEXT, font=self.font_sm, anchor="ra")
            if volts > 0:
                d.text((sx + sw, sy), f"{volts:.3f}V",
                       fill=C_ACCENT, font=self.font_sm, anchor="ra")
            sy += 18

        # PSU fan + temps on one clean line
        fan = psu.get('fan_rpm')
        vrm_c = psu.get('temp_vrm', 0)
        case_c = psu.get('temp_case', 0)
        if fan is not None:
            fan_str = f"{fan:.0f} RPM" if fan > 0 else "Fan off"
            d.text((sx, sy), fan_str, fill=C_DIM, font=self.font_sm)
        if vrm_c > 0 or case_c > 0:
            temp_parts = []
            if vrm_c > 0:
                temp_parts.append(f"{vrm_c * 9/5 + 32:.0f}\u00b0")
            if case_c > 0:
                temp_parts.append(f"{case_c * 9/5 + 32:.0f}\u00b0F")
            d.text((sx + sw, sy), "/".join(temp_parts),
                   fill=C_DIM, font=self.font_sm, anchor="ra")

    def _draw_network_panel(self, d, px, py):
        """Network panel: compact sparklines + top processes."""
        self._draw_panel(d, px, py, label="Network")

        sx = px + 16
        sw = PANEL_W - 32
        sy = py + 34

        # ── Upload (compact) ──
        d.text((sx, sy), "Up", fill=C_DIM, font=self.font_sm)
        d.text((sx + sw, sy), self._fmt_rate(self._net_up_rate),
               fill=C_GREEN, font=self.font_sm, anchor="ra")
        self._draw_sparkline(d, sx, sy + 18, sw, 40,
                             list(self._net_up_history),
                             C_GREEN, (20, 55, 30))

        # ── Download (compact) ──
        sy += 62
        d.text((sx, sy), "Dn", fill=C_DIM, font=self.font_sm)
        d.text((sx + sw, sy), self._fmt_rate(self._net_dn_rate),
               fill=C_BLUE, font=self.font_sm, anchor="ra")
        self._draw_sparkline(d, sx, sy + 18, sw, 40,
                             list(self._net_dn_history),
                             C_BLUE, (20, 30, 60))

        # ── Top Processes ──
        sy += 66
        d.line([(sx, sy), (sx + sw, sy)], fill=C_BORDER, width=1)
        sy += 6
        d.text((sx, sy), "Top Processes", fill=C_DIM, font=self.font_sm)
        sy += 18

        for cpu, name in self._top_procs:
            disp = name[:14] + '\u2026' if len(name) > 15 else name
            d.text((sx, sy), disp, fill=C_TEXT, font=self.font_sm)
            d.text((sx + sw, sy), f"{cpu:4.1f}%",
                   fill=self._bar_color(min(cpu, 100)),
                   font=self.font_sm, anchor="ra")
            sy += 18

    def _draw_storage_panel(self, d, px, py):
        """Storage panel: partition usage bars (data from background thread)."""
        self._draw_panel(d, px, py, label="Storage")

        filtered = self._partitions

        sx = px + 16
        sw = PANEL_W - 32
        sy = py + 34

        if not filtered:
            d.text((sx, sy), "No partitions",
                   fill=C_DIM, font=self.font_stat)
            return

        for mount, usage in filtered:
            if sy > py + ROW_H - 46:
                break

            pct = usage.percent
            used_gb = usage.used / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)

            # Mount point and percentage
            mount_disp = mount if len(mount) <= 12 else mount[:11] + "\u2026"
            d.text((sx, sy), mount_disp, fill=C_TEXT, font=self.font_stat)
            d.text((sx + sw, sy), f"{pct:.1f}%",
                   fill=self._bar_color(pct), font=self.font_stat, anchor="ra")
            sy += 22

            # Progress bar
            self._draw_progress_bar(d, sx, sy, sw, 14, pct)
            sy += 18

            # Size text
            d.text((sx, sy), f"{used_gb:.1f}/{total_gb:.1f} GB",
                   fill=C_DIM, font=self.font_sm)
            sy += 22

    # ── Main render ─────────────────────────────────────────────────

    def render(self):
        """Render a single frame of the graphical dashboard."""
        img = Image.new('RGB', (WIDTH, HEIGHT), C_BG)
        d = ImageDraw.Draw(img)

        self._update_rates()

        # Header
        self._draw_header(d)

        # Top row: CPU, GPU, Temperatures
        self._draw_cpu_panel(d, COL_X[0], ROW_Y[0])
        self._draw_gpu_panel(d, COL_X[1], ROW_Y[0])
        self._draw_temps_panel(d, COL_X[2], ROW_Y[0])

        # Bottom row: Memory, Network, Storage
        self._draw_memory_panel(d, COL_X[0], ROW_Y[1])
        self._draw_network_panel(d, COL_X[1], ROW_Y[1])
        self._draw_storage_panel(d, COL_X[2], ROW_Y[1])

        return img


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
    suspended = threading.Event()

    def sigint_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, sigint_handler)

    # ── Sleep/wake handling via systemd-logind D-Bus ──
    dbus_thread = None
    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        DBusGMainLoop(set_as_default=True)

        saved_brightness = [None]

        def on_prepare_for_sleep(going_to_sleep):
            if going_to_sleep:
                print("\n  System suspending — display off")
                try:
                    saved_brightness[0] = wigi.get_brightness()
                    wigi.set_brightness(0)
                except Exception:
                    pass
                suspended.set()
            else:
                print("\n  System resumed — reconnecting")
                suspended.clear()

        bus = dbus.SystemBus()
        bus.add_signal_receiver(
            on_prepare_for_sleep,
            signal_name='PrepareForSleep',
            dbus_interface='org.freedesktop.login1.Manager',
            bus_name='org.freedesktop.login1',
        )

        loop = GLib.MainLoop()
        dbus_thread = threading.Thread(target=loop.run, daemon=True)
        dbus_thread.start()
        print("  Sleep/wake detection enabled (systemd-logind)")
    except Exception as e:
        print(f"  Sleep/wake detection unavailable: {e}")

    try:
        print(f"System monitor running (interval={interval}s). Press Ctrl+C to stop.")
        frame_count = 0
        while running:
            # If system just resumed, reconnect the USB device
            if suspended.is_set():
                time.sleep(0.5)
                continue
            if not wigi._initialized:
                print("\n  Reconnecting to display...", end='', flush=True)
                if wigi.reconnect():
                    print(" OK")
                    if saved_brightness[0] is not None:
                        try:
                            wigi.set_brightness(saved_brightness[0])
                        except Exception:
                            pass
                        saved_brightness[0] = None
                else:
                    print(" FAILED — retrying in 2s")
                    time.sleep(2)
                    continue

            t0 = time.time()
            img = monitor.render()
            t_render = time.time() - t0

            t1 = time.time()
            try:
                wigi.send_image(img)
            except (usb.core.USBError, usb.core.USBTimeoutError):
                # USB error — device probably lost after sleep/disconnect
                print("\n  USB error — will reconnect")
                wigi.close()
                time.sleep(1)
                continue
            t_send = time.time() - t1

            frame_count += 1
            total = (t_render + t_send) * 1000
            fps = 1000 / total if total > 0 else 0
            print(f"\r  Frame {frame_count}: "
                  f"render={t_render*1000:.0f}ms "
                  f"send={t_send*1000:.0f}ms "
                  f"({fps:.1f} fps)     ", end='', flush=True)

            elapsed = time.time() - t0
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0 and running:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopped.")
        monitor.stop()
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
    p_mon.add_argument('--interval', type=float, default=0.5,
                       help='Update interval in seconds (default: 0.5)')
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
