import rumps
import psutil
import subprocess
import urllib.request
import json
import re
import threading
import time
import os
import io
import ctypes
import ctypes.util
import objc
from collections import deque
from PIL import Image, ImageDraw, ImageFont

from AppKit import (
    NSImage, NSSize,
    NSAttributedString,
    NSForegroundColorAttributeName, NSFontAttributeName,
    NSFont, NSMenuItem,
    NSData,
)
from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMES_DIR = os.path.join(BASE_DIR, "frames")
FRAME_PATHS = [os.path.abspath(os.path.join(FRAMES_DIR, f"frame_{i}.png")) for i in range(5)]

OLLAMA_API  = "http://localhost:11434"
HISTORY_LEN = 60

GRAPH_W, GRAPH_H = 260, 52
CPU_COLOR = (59, 153, 252)   # 파랑
GPU_COLOR = (50, 215, 115)   # 초록
BG_COLOR  = (0, 0, 0, 15)    # 거의 투명

# ── IOKit GPU Stats Setup ──────────────────────────────────────────
iokit_path = ctypes.util.find_library("IOKit")
iokit = ctypes.CDLL(iokit_path)
cf_path = ctypes.util.find_library("CoreFoundation")
cf = ctypes.CDLL(cf_path)

iokit.IOServiceMatching.restype = ctypes.c_void_p
iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
iokit.IOServiceGetMatchingServices.restype = ctypes.c_int
iokit.IOServiceGetMatchingServices.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
iokit.IOIteratorNext.restype = ctypes.c_uint
iokit.IOIteratorNext.argtypes = [ctypes.c_uint]
iokit.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
iokit.IORegistryEntryCreateCFProperty.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
iokit.IOObjectRelease.restype = ctypes.c_int
iokit.IOObjectRelease.argtypes = [ctypes.c_uint]
cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
cf.CFRelease.restype = None
cf.CFRelease.argtypes = [ctypes.c_void_p]

_key_perf_stats = cf.CFStringCreateWithCString(None, b"PerformanceStatistics", 0)

# ── IOHIDEventSystem Thermal Setup (Apple Silicon) ─────────────────
iokit.IOHIDEventSystemClientCreate.restype = ctypes.c_void_p
iokit.IOHIDEventSystemClientCreate.argtypes = [ctypes.c_void_p]
iokit.IOHIDEventSystemClientCopyServices.restype = ctypes.c_void_p
iokit.IOHIDEventSystemClientCopyServices.argtypes = [ctypes.c_void_p]
iokit.IOHIDServiceClientCopyProperty.restype = ctypes.c_void_p
iokit.IOHIDServiceClientCopyProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDServiceClientCopyEvent.restype = ctypes.c_void_p
iokit.IOHIDServiceClientCopyEvent.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]
iokit.IOHIDEventGetFloatValue.restype = ctypes.c_double
iokit.IOHIDEventGetFloatValue.argtypes = [ctypes.c_void_p, ctypes.c_int32]
cf.CFStringGetCString.restype = ctypes.c_bool
cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]

_HIDEVT_TEMPERATURE = 15
_HIDEVT_TEMP_FIELD  = (_HIDEVT_TEMPERATURE << 16) | 0
_hid_product_key = cf.CFStringCreateWithCString(None, b"Product", 0)
_hid_client = iokit.IOHIDEventSystemClientCreate(None)

def _hid_temp(svc_ptr) -> float:
    evt = iokit.IOHIDServiceClientCopyEvent(svc_ptr, _HIDEVT_TEMPERATURE, 0, 0)
    if not evt:
        return None
    val = iokit.IOHIDEventGetFloatValue(evt, _HIDEVT_TEMP_FIELD)
    cf.CFRelease(evt)
    return val if 0 < val < 150 else None

def get_thermal_stats() -> dict:
    try:
        svc_ptr_arr = iokit.IOHIDEventSystemClientCopyServices(_hid_client)
        if not svc_ptr_arr:
            return {"cpu_temp": 0.0, "gpu_temp": 0.0}
        svcs = objc.objc_object(c_void_p=svc_ptr_arr)
        buf = ctypes.create_string_buffer(64)
        tdie_vals, tdev_vals = [], []
        for svc in svcs:
            svc_ptr = ctypes.c_void_p(objc.pyobjc_id(svc))
            name_ptr = iokit.IOHIDServiceClientCopyProperty(svc_ptr, _hid_product_key)
            if not name_ptr:
                continue
            cf.CFStringGetCString(name_ptr, buf, 64, 0x08000100)
            name = buf.value.decode("utf-8", errors="ignore")
            cf.CFRelease(name_ptr)
            t = _hid_temp(svc_ptr)
            if t is None:
                continue
            if "tdie" in name:
                tdie_vals.append(t)
            elif "tdev" in name:
                tdev_vals.append(t)
        cf.CFRelease(svc_ptr_arr)
        # tdev = CPU die clusters, tdie = GPU die (confirmed vs macmon)
        cpu_t = max(tdev_vals) if tdev_vals else 0.0
        gpu_t = max(tdie_vals) if tdie_vals else 0.0
        return {"cpu_temp": cpu_t, "gpu_temp": gpu_t}
    except Exception:
        return {"cpu_temp": 0.0, "gpu_temp": 0.0}


def animation_interval(load_pct):
    if load_pct >= 80:   return 0.03
    elif load_pct >= 60: return 0.06
    elif load_pct >= 40: return 0.12
    elif load_pct >= 20: return 0.25
    else:                return 0.5


def get_gpu_stats():
    matching = iokit.IOServiceMatching(b"AGXAccelerator")
    iterator = ctypes.c_uint()
    err = iokit.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator))
    if err != 0: return {"device": 0, "renderer": 0, "tiler": 0}
    stats = {"device": 0, "renderer": 0, "tiler": 0}
    service = iokit.IOIteratorNext(iterator)
    if service:
        prop_ptr = iokit.IORegistryEntryCreateCFProperty(service, _key_perf_stats, None, 0)
        if prop_ptr:
            try:
                perf_stats = objc.objc_object(c_void_p=prop_ptr)
                stats = {"device": perf_stats.get("Device Utilization %", 0), "renderer": perf_stats.get("Renderer Utilization %", 0), "tiler": perf_stats.get("Tiler Utilization %", 0)}
            except Exception: pass
            cf.CFRelease(prop_ptr)
        iokit.IOObjectRelease(service)
    iokit.IOObjectRelease(iterator)
    return stats


def _diskutil_used_bytes(path):
    try:
        out = subprocess.run(["diskutil", "info", path], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"Volume Used Space:\s+[\d.]+ \w+\s+\((\d+) Bytes\)", out)
        if m: return int(m.group(1))
    except Exception: pass
    return 0


def _diskutil_container_bytes(path):
    try:
        out = subprocess.run(["diskutil", "info", path], capture_output=True, text=True, timeout=3).stdout
        total = re.search(r"Container Total Space:\s+[\d.]+ \w+\s+\((\d+) Bytes\)", out)
        free  = re.search(r"Container Free Space:\s+[\d.]+ \w+\s+\((\d+) Bytes\)", out)
        if total and free: return int(total.group(1)), int(free.group(1))
    except Exception: pass
    return 0, 0


def get_disk_stats():
    sys_used, data_used = _diskutil_used_bytes("/"), _diskutil_used_bytes("/System/Volumes/Data")
    total_b, _ = _diskutil_container_bytes("/")
    used_b = sys_used + data_used
    total_gb, used_gb = total_b / 1e9, used_b / 1e9
    pct = min(used_gb / total_gb * 100, 100) if total_gb > 0 else 0
    return {"used_gb": used_gb, "total_gb": total_gb, "pct": pct}


def get_battery_stats():
    bat = psutil.sensors_battery()
    if bat is None: return None
    plugged, secs = bat.power_plugged, bat.secsleft
    if plugged: remain = "충전 중"
    elif secs == psutil.POWER_TIME_UNLIMITED or secs < 0: remain = "--"
    else:
        h, m = divmod(secs // 60, 60)
        remain = f"{h}시간 {m}분 남음"
    return {"pct": bat.percent, "plugged": plugged, "remain": remain}


_net_prev, _net_prev_time = None, None

def get_network_stats():
    global _net_prev, _net_prev_time
    now, counters = time.time(), psutil.net_io_counters()
    if _net_prev is None or _net_prev_time is None:
        _net_prev, _net_prev_time = counters, now
        return {"up_kb": 0.0, "down_kb": 0.0, "iface": ""}
    dt = now - _net_prev_time
    up, down = (counters.bytes_sent - _net_prev.bytes_sent) / dt / 1024, (counters.bytes_recv - _net_prev.bytes_recv) / dt / 1024
    _net_prev, _net_prev_time = counters, now
    iface = ""
    try:
        stats = psutil.net_if_stats()
        for name, s in stats.items():
            if s.isup and name not in ("lo0",):
                iface = name
                break
    except Exception: pass
    return {"up_kb": max(up, 0), "down_kb": max(down, 0), "iface": iface}


def get_ollama_api_info():
    try:
        req = urllib.request.urlopen(f"{OLLAMA_API}/api/ps", timeout=1)
        data = json.loads(req.read())
        models = data.get("models", [])
        if not models: return None
        model = max(models, key=lambda m: m.get("size_vram", 0))
        return {
            "name": model["name"],
            "vram_gb": model.get("size_vram", 0) / 1024**3,
            "size_gb": model.get("size", 0) / 1024**3,
            "context": model.get("context_length", 0),
        }
    except Exception: return None


class OllamaProcessTracker:
    def __init__(self):
        self._procs: dict[int, psutil.Process] = {}
        self._last_scan_time = 0

    def update(self):
        now = time.time()
        if now - self._last_scan_time > 5:
            self._last_scan_time = now
            current_pids = set()
            for p in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmd = " ".join(p.info['cmdline'] or [])
                    if 'ollama' not in cmd.lower(): continue
                    pid = p.pid
                    current_pids.add(pid)
                    if pid not in self._procs:
                        proc = psutil.Process(pid)
                        proc.cpu_percent()
                        self._procs[pid] = proc
                except (psutil.NoSuchProcess, psutil.AccessDenied): pass
            for pid in list(self._procs):
                if pid not in current_pids: del self._procs[pid]
        else:
            for pid in list(self._procs):
                if not self._procs[pid].is_running(): del self._procs[pid]

    def stats(self):
        best, best_mem, total_cpu = None, 0, 0.0
        for pid, proc in list(self._procs.items()):
            try:
                cpu, mem = proc.cpu_percent(), proc.memory_info().rss
                total_cpu += cpu
                if mem > best_mem:
                    best_mem = mem
                    best = {"cpu": cpu, "mem_mb": mem / 1024**2}
            except (psutil.NoSuchProcess, psutil.AccessDenied): del self._procs[pid]
        if best: best["total_cpu"] = total_cpu
        return best


class HistoryGraph:
    def __init__(self):
        self._cpu, self._gpu = deque([0.0]*HISTORY_LEN, HISTORY_LEN), deque([0.0]*HISTORY_LEN, HISTORY_LEN)

    def push(self, cpu, gpu):
        self._cpu.append(cpu); self._gpu.append(gpu)

    def render_nsimage(self):
        pad, w, h = 8, GRAPH_W * 2, GRAPH_H * 2
        gw, gh = w - pad * 2, h - pad * 2 - 20
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([pad, pad + 20, w - pad, h - pad], radius=6, fill=(0, 0, 0, 18))
        for pct in (25, 50, 75):
            y = h - pad - int(gh * pct / 100)
            d.line([(pad, y), (w - pad, y)], fill=(128, 128, 128, 50), width=1)
        def draw_line(hist, color):
            pts = [(pad + int(gw * i / (HISTORY_LEN - 1)), h - pad - int(gh * min(val, 100) / 100)) for i, val in enumerate(list(hist))]
            if len(pts) >= 2: d.line(pts, fill=color + (220,), width=3)
        draw_line(self._gpu, GPU_COLOR); draw_line(self._cpu, CPU_COLOR)
        try: font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        except Exception: font = ImageFont.load_default()
        d.ellipse([pad, pad+2, pad+14, pad+16], fill=CPU_COLOR+(230,))
        d.text((pad+20, pad+1), "CPU", font=font, fill=CPU_COLOR+(230,))
        d.ellipse([pad+80, pad+2, pad+94, pad+16], fill=GPU_COLOR+(230,))
        d.text((pad+100, pad+1), "GPU", font=font, fill=GPU_COLOR+(230,))
        buf = io.BytesIO(); img.save(buf, format="PNG")
        data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
        ns_img = NSImage.alloc().initWithData_(data)
        ns_img.setSize_(NSSize(GRAPH_W, GRAPH_H))
        return ns_img


def _make_astr(title, bold=False, font_size=13, dark=True, mono=False):
    from AppKit import NSColor
    font = (NSFont.fontWithName_size_("Menlo-Bold" if bold else "Menlo-Regular", font_size) if mono else (NSFont.boldSystemFontOfSize_ if bold else NSFont.systemFontOfSize_)(font_size))
    color = NSColor.colorWithRed_green_blue_alpha_(0.05, 0.05, 0.05, 1.0) if dark else NSColor.secondaryLabelColor()
    return NSAttributedString.alloc().initWithString_attributes_(title, {NSFontAttributeName: font, NSForegroundColorAttributeName: color})


def set_title(item, title, bold=False, font_size=13, sub=False, mono=False):
    item._menuitem.setAttributedTitle_(_make_astr(title, bold, font_size, not sub, mono))


def _threshold_color(val: float, warn: float, crit: float):
    """val이 crit 이상이면 빨강, warn 이상이면 주황, 미만이면 None(기본색)."""
    from AppKit import NSColor
    if val >= crit:
        return NSColor.colorWithRed_green_blue_alpha_(0.9, 0.1, 0.1, 1.0)
    if val >= warn:
        return NSColor.colorWithRed_green_blue_alpha_(0.95, 0.5, 0.0, 1.0)
    return None


def _temp_color(temp: float):
    return _threshold_color(temp, warn=70, crit=85)


def _usage_color(pct: float):
    return _threshold_color(pct, warn=70, crit=85)


def set_title_segments(item, segments, bold=False, font_size=13, mono=False):
    """segments: list of (text, color_or_None). 각 구간에 독립적인 색상 적용."""
    from AppKit import NSMutableAttributedString
    result = NSMutableAttributedString.alloc().initWithString_attributes_("", {})
    for text, color in segments:
        if not text:
            continue
        astr = _make_astr(text, bold, font_size, dark=True, mono=mono)
        if color:
            mut = NSMutableAttributedString.alloc().initWithAttributedString_(astr)
            mut.addAttribute_value_range_(NSForegroundColorAttributeName, color, (0, len(text)))
            result.appendAttributedString_(mut)
        else:
            result.appendAttributedString_(astr)
    item._menuitem.setAttributedTitle_(result)


def make_bar(pct, width=8):
    filled = round(min(pct, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)


class MonitorApp(rumps.App):
    def __init__(self):
        super().__init__("", quit_button=None)
        self.template = True

        self._cpu, self._mem, self._frame, self._stats_counter = 0.0, 0.0, 0, 0
        self._gpu  = {"device": 0, "renderer": 0, "tiler": 0}
        self._disk = {"used_gb": 0.0, "total_gb": 0.0, "pct": 0.0}
        self._net  = {"up_kb": 0.0, "down_kb": 0.0, "iface": ""}
        self._bat, self._ollama_proc, self._ollama_api = None, None, None
        self._thermal = {"cpu_temp": 0.0, "gpu_temp": 0.0}
        self._lock = threading.Lock()
        self._tracker, self._graph = OllamaProcessTracker(), HistoryGraph()
        self._menu_open = False
        self._needs_update = False

        self._graph_nsitem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        self._graph_nsitem.setEnabled_(False)
        self._graph_inserted = False

        self._item_cpu, self._item_gpu, self._item_gpu_sub = rumps.MenuItem(""), rumps.MenuItem(""), rumps.MenuItem("")
        self._item_mem, self._item_disk, self._item_disk_sub = rumps.MenuItem(""), rumps.MenuItem(""), rumps.MenuItem("")
        self._item_bat, self._item_bat_sub = rumps.MenuItem(""), rumps.MenuItem("")
        self._item_net, self._item_net_sub = rumps.MenuItem(""), rumps.MenuItem("")
        self._item_ol_model, self._item_ol_vram, self._item_ol_cpu = rumps.MenuItem(""), rumps.MenuItem(""), rumps.MenuItem("")

        self.menu = [
            self._item_cpu, self._item_gpu, self._item_gpu_sub, rumps.separator,
            self._item_mem, rumps.separator, self._item_disk, self._item_disk_sub, rumps.separator,
            self._item_bat, self._item_bat_sub, rumps.separator, self._item_net, self._item_net_sub, rumps.separator,
            self._item_ol_model, self._item_ol_vram, self._item_ol_cpu, rumps.separator,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        self._setup_menu_observers()
        self._start_timer(self._animate, 0.1)
        
        # Start background stats collection
        self._stats_thread = threading.Thread(target=self._stats_loop, daemon=True)
        self._stats_thread.start()

    def _setup_menu_observers(self):
        from Foundation import NSNotificationCenter
        nc = NSNotificationCenter.defaultCenter()
        nc.addObserver_selector_name_object_(self, "_menu_will_open:", "NSMenuDidBeginTrackingNotification", None)
        nc.addObserver_selector_name_object_(self, "_menu_did_close:", "NSMenuDidEndTrackingNotification", None)

    def _menu_will_open_(self, _):
        self._menu_open = True
        self._update_ui()

    def _menu_did_close_(self, _):
        self._menu_open = False

    def _start_timer(self, callback, interval):
        t = rumps.Timer(callback, interval)
        t.start()
        NSRunLoop.currentRunLoop().addTimer_forMode_(t._nstimer, NSRunLoopCommonModes)

    def _set_icon(self, path):
        try:
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img:
                orig = img.size()
                h = 16.0
                w = h * orig.width / orig.height
                img.setSize_(NSSize(w, h))
                img.setTemplate_(True)
                self._nsapp.nsstatusitem.setImage_(img)
        except Exception: pass

    def _animate(self, timer):
        with self._lock:
            cpu, gpu_pct = self._cpu, self._gpu["device"]
            plugged = self._bat["plugged"] if self._bat else True
        
        base_interval = animation_interval(max(cpu, gpu_pct))
        new_interval = base_interval * (1.5 if not plugged and max(cpu, gpu_pct) < 20 else 1.0)

        if abs(timer.interval - new_interval) > 0.001:
            timer.interval = new_interval
            try: NSRunLoop.currentRunLoop().addTimer_forMode_(timer._nstimer, NSRunLoopCommonModes)
            except Exception: pass
        
        self._frame = (self._frame + 1) % len(FRAME_PATHS)
        self._set_icon(FRAME_PATHS[self._frame])
        
        if self._menu_open:
            self._insert_graph()
            if self._needs_update:
                self._update_ui()
                self._needs_update = False

    def _insert_graph(self):
        if self._graph_inserted: return
        try:
            ns_menu = self._item_cpu._menuitem.menu()
            if ns_menu:
                idx = ns_menu.indexOfItem_(self._item_cpu._menuitem)
                ns_menu.insertItem_atIndex_(self._graph_nsitem, idx)
                self._graph_inserted = True
        except Exception: pass

    def _stats_loop(self):
        """Background thread for data collection."""
        counter = 0
        while True:
            with self._lock:
                plugged = self._bat["plugged"] if self._bat else True
            
            # Adaptive interval
            interval = 2.0 if not plugged else 1.0
            
            # 1. High frequency stats
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            gpu = get_gpu_stats()
            net = get_network_stats()
            self._tracker.update()
            ol_proc = self._tracker.stats()
            
            # 2. Medium frequency stats (every 3s or 6s)
            bat, ol_api, thermal = None, None, None
            ollama_freq = 3 if plugged else 6
            _ollama_checked = False
            if counter % ollama_freq == 0:
                bat = get_battery_stats()
                ol_api = get_ollama_api_info()
                thermal = get_thermal_stats()
                _ollama_checked = True
            
            # 3. Low frequency stats (every 30s)
            disk = None
            if counter % 30 == 0:
                disk = get_disk_stats()
            
            with self._lock:
                self._cpu, self._mem, self._gpu, self._net = cpu, mem, gpu, net
                self._ollama_proc = ol_proc
                if bat is not None: self._bat = bat
                if _ollama_checked: self._ollama_api = ol_api
                if thermal is not None: self._thermal = thermal
                if disk is not None: self._disk = disk
                self._graph.push(cpu, gpu["device"])
                self._needs_update = True
            
            counter += 1
            time.sleep(interval)

    def _update_ui(self):
        """Update menu items and graph image. Should be called on main thread."""
        self._graph_nsitem.setImage_(self._graph.render_nsimage())
        self._update_menu()

    def _update_menu(self):
        with self._lock: cpu, mem, gpu, disk, bat, net, ol_proc, ol_api, thermal = self._cpu, self._mem, self._gpu, self._disk, self._bat, self._net, self._ollama_proc, self._ollama_api, self._thermal
        LBL = 9
        cpu_t, gpu_t = thermal.get("cpu_temp", 0.0), thermal.get("gpu_temp", 0.0)
        cpu_t_str = f"  {cpu_t:.0f}°C" if cpu_t > 0 else ""
        gpu_t_str = f"  {gpu_t:.0f}°C" if gpu_t > 0 else ""
        gpu_dev = gpu.get('device', 0)
        disk_pct = disk.get('pct', 0)
        set_title_segments(self._item_cpu, [
            (f"{'CPU':<{LBL}}{make_bar(cpu)}  ", None),
            (f"{cpu:.1f}%", _usage_color(cpu)),
            (cpu_t_str, _temp_color(cpu_t)),
        ], bold=True, mono=True)
        set_title_segments(self._item_gpu, [
            (f"{'GPU':<{LBL}}{make_bar(gpu_dev)}  ", None),
            (f"{gpu_dev}%", _usage_color(gpu_dev)),
            (gpu_t_str, _temp_color(gpu_t)),
        ], bold=True, mono=True)
        set_title(self._item_gpu_sub, f"  Renderer {gpu.get('renderer', 0)}%   Tiler {gpu.get('tiler', 0)}%", font_size=11, sub=True)
        set_title_segments(self._item_mem, [
            (f"{'Memory':<{LBL}}{make_bar(mem)}  ", None),
            (f"{mem:.1f}%", _usage_color(mem)),
        ], bold=True, mono=True)
        set_title_segments(self._item_disk, [
            (f"{'Disk':<{LBL}}{make_bar(disk_pct)}  ", None),
            (f"{disk_pct:.1f}%", _usage_color(disk_pct)),
        ], bold=True, mono=True)
        set_title(self._item_disk_sub, f"  {disk.get('used_gb', 0.0):.1f} GB / {disk.get('total_gb', 0.0):.1f} GB 사용 중", font_size=11, sub=True)
        if bat:
            bat_pct = bat['pct']
            if bat['plugged']:
                from AppKit import NSColor
                bat_color = NSColor.colorWithRed_green_blue_alpha_(0.1, 0.75, 0.2, 1.0) if bat_pct >= 80 else None
            else:
                bat_color = _threshold_color(100 - bat_pct, warn=80, crit=90)
            set_title_segments(self._item_bat, [
                (f"{'Battery':<{LBL}}{make_bar(bat_pct)}  {'⚡ ' if bat['plugged'] else ''}", None),
                (f"{bat_pct:.0f}%", bat_color),
            ], bold=True, mono=True)
            set_title(self._item_bat_sub, f"  {bat['remain']}", font_size=11, sub=True)
        else: set_title(self._item_bat, f"{'Battery':<{LBL}}N/A", bold=True, mono=True)
        def fmt_kb(kb): return f"{kb/1024:.1f} MB/s" if kb >= 1024 else f"{kb:.0f} KB/s"
        set_title(self._item_net, f"{'Network':<{LBL}}↑ {fmt_kb(net['up_kb'])}  ↓ {fmt_kb(net['down_kb'])}{' ('+net['iface']+')' if net['iface'] else ''}", bold=True, mono=True)
        if ol_api:
            set_title(self._item_ol_model, f"Ollama  {ol_api['name']}  (ctx {ol_api['context']:,})", bold=True)
            set_title(self._item_ol_vram, f"  VRAM {ol_api['vram_gb']:.1f} GB  /  Model {ol_api['size_gb']:.1f} GB", font_size=11, sub=True)
        else:
            set_title(self._item_ol_model, "Ollama: 로드된 모델 없음", sub=True)
            set_title(self._item_ol_vram, "", font_size=11, sub=True)
            set_title(self._item_ol_cpu, "", font_size=11, sub=True)
        if ol_api and ol_proc:
            set_title(self._item_ol_cpu, f"  CPU {ol_proc['total_cpu']:.1f}%   MEM {ol_proc['mem_mb']:.0f} MB", font_size=11, sub=True)

    def quit_app(self, _): rumps.quit_application()

if __name__ == "__main__":
    MonitorApp().run()
