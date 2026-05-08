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
from collections import deque
from PIL import Image, ImageDraw, ImageFont

from AppKit import (
    NSImage, NSSize,
    NSAttributedString,
    NSForegroundColorAttributeName, NSFontAttributeName,
    NSFont, NSMenuItem,
    NSData,
)
from Foundation import NSRunLoop, NSRunLoopCommonModes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMES_DIR = os.path.join(BASE_DIR, "frames")
FRAME_PATHS = [os.path.join(FRAMES_DIR, f"frame_{i}.png") for i in range(5)]

OLLAMA_API  = "http://localhost:11434"
HISTORY_LEN = 60

GRAPH_W, GRAPH_H = 260, 52
CPU_COLOR = (59, 153, 252)   # 파랑
GPU_COLOR = (50, 215, 115)   # 초록
BG_COLOR  = (0, 0, 0, 15)    # 거의 투명


def animation_interval(load_pct):
    if load_pct >= 80:   return 0.027
    elif load_pct >= 60: return 0.07
    elif load_pct >= 40: return 0.15
    elif load_pct >= 20: return 0.35
    else:                return 0.7


def get_gpu_stats():
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-c", "AGXAccelerator"],
            capture_output=True, text=True, timeout=1
        )
        device   = re.search(r'"Device Utilization %"\s*=\s*(\d+)', result.stdout)
        renderer = re.search(r'"Renderer Utilization %"\s*=\s*(\d+)', result.stdout)
        tiler    = re.search(r'"Tiler Utilization %"\s*=\s*(\d+)', result.stdout)
        return {
            "device":   int(device.group(1))   if device   else 0,
            "renderer": int(renderer.group(1)) if renderer else 0,
            "tiler":    int(tiler.group(1))    if tiler    else 0,
        }
    except Exception:
        return {"device": 0, "renderer": 0, "tiler": 0}


def _diskutil_used_bytes(path):
    """diskutil info로 볼륨의 실제 사용 바이트 반환."""
    try:
        out = subprocess.run(
            ["diskutil", "info", path],
            capture_output=True, text=True, timeout=3
        ).stdout
        m = re.search(r"Volume Used Space:\s+[\d.]+ \w+\s+\((\d+) Bytes\)", out)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0


def _diskutil_container_bytes(path):
    """diskutil info로 컨테이너 전체/여유 바이트 반환."""
    try:
        out = subprocess.run(
            ["diskutil", "info", path],
            capture_output=True, text=True, timeout=3
        ).stdout
        total = re.search(r"Container Total Space:\s+[\d.]+ \w+\s+\((\d+) Bytes\)", out)
        free  = re.search(r"Container Free Space:\s+[\d.]+ \w+\s+\((\d+) Bytes\)", out)
        if total and free:
            return int(total.group(1)), int(free.group(1))
    except Exception:
        pass
    return 0, 0


def get_disk_stats():
    sys_used  = _diskutil_used_bytes("/")
    data_used = _diskutil_used_bytes("/System/Volumes/Data")
    total_b, _ = _diskutil_container_bytes("/")
    used_b = sys_used + data_used
    total_gb = total_b / 1e9
    used_gb  = used_b  / 1e9
    pct = min(used_gb / total_gb * 100, 100) if total_gb > 0 else 0
    return {"used_gb": used_gb, "total_gb": total_gb, "pct": pct}


def get_battery_stats():
    bat = psutil.sensors_battery()
    if bat is None:
        return None
    plugged = bat.power_plugged
    secs    = bat.secsleft
    if secs == psutil.POWER_TIME_UNLIMITED or secs < 0:
        remain = "충전 중" if plugged else "--"
    else:
        h, m = divmod(secs // 60, 60)
        remain = f"{h}시간 {m}분 남음"
    return {
        "pct":     bat.percent,
        "plugged": plugged,
        "remain":  remain,
    }


_net_prev = None
_net_prev_time = None

def get_network_stats():
    global _net_prev, _net_prev_time
    now   = time.time()
    counters = psutil.net_io_counters()
    if _net_prev is None or _net_prev_time is None:
        _net_prev = counters
        _net_prev_time = now
        return {"up_kb": 0.0, "down_kb": 0.0, "iface": ""}
    dt = now - _net_prev_time
    up   = (counters.bytes_sent - _net_prev.bytes_sent) / dt / 1024
    down = (counters.bytes_recv - _net_prev.bytes_recv) / dt / 1024
    _net_prev      = counters
    _net_prev_time = now
    # 활성 인터페이스 이름
    iface = ""
    try:
        stats = psutil.net_if_stats()
        for name, s in stats.items():
            if s.isup and name not in ("lo0",):
                iface = name
                break
    except Exception:
        pass
    return {"up_kb": max(up, 0), "down_kb": max(down, 0), "iface": iface}


def get_ollama_api_info():
    try:
        req  = urllib.request.urlopen(f"{OLLAMA_API}/api/ps", timeout=1)
        data = json.loads(req.read())
        models = data.get("models", [])
        if not models:
            return None
        model = max(models, key=lambda m: m.get("size_vram", 0))
        return {
            "name":    model["name"],
            "vram_gb": model.get("size_vram", 0) / 1024**3,
            "size_gb": model.get("size", 0)      / 1024**3,
            "context": model.get("context_length", 0),
        }
    except Exception:
        return None


class OllamaProcessTracker:
    def __init__(self):
        self._procs: dict[int, psutil.Process] = {}
        self._last_scan_time = 0

    def update(self):
        now = time.time()
        # 프로세스 목록 스캔은 5초에 한 번만 수행 (부하 경감)
        if now - self._last_scan_time > 5:
            self._last_scan_time = now
            current_pids = set()
            for p in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmd = " ".join(p.info['cmdline'] or [])
                    if 'ollama' not in cmd.lower():
                        continue
                    pid = p.pid
                    current_pids.add(pid)
                    if pid not in self._procs:
                        proc = psutil.Process(pid)
                        proc.cpu_percent()
                        self._procs[pid] = proc
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            # 사라진 프로세스 정리
            for pid in list(self._procs):
                if pid not in current_pids:
                    del self._procs[pid]
        else:
            # 스캔 사이에는 기존 프로세스 생존 여부만 가볍게 확인
            for pid in list(self._procs):
                if not self._procs[pid].is_running():
                    del self._procs[pid]

    def stats(self):
        best, best_mem, total_cpu = None, 0, 0.0
        for pid, proc in list(self._procs.items()):
            try:
                cpu = proc.cpu_percent()
                mem = proc.memory_info().rss
                total_cpu += cpu
                if mem > best_mem:
                    best_mem = mem
                    best = {"cpu": cpu, "mem_mb": mem / 1024**2}
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                del self._procs[pid]
        if best:
            best["total_cpu"] = total_cpu
        return best


# ── 히스토리 그래프 (PIL로 PNG 생성 → NSImage) ───────────────────────
class HistoryGraph:
    def __init__(self):
        self._cpu = deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self._gpu = deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN)

    def push(self, cpu, gpu):
        self._cpu.append(cpu)
        self._gpu.append(gpu)

    def render_nsimage(self):
        pad = 8
        w, h = GRAPH_W * 2, GRAPH_H * 2   # @2x
        gw = w - pad * 2
        gh = h - pad * 2 - 20             # 위쪽 범례 공간

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # 배경 라운드 박스
        d.rounded_rectangle([pad, pad + 20, w - pad, h - pad],
                             radius=6, fill=(0, 0, 0, 18))

        # 그리드 (25/50/75%)
        for pct in (25, 50, 75):
            y = h - pad - int(gh * pct / 100)
            d.line([(pad, y), (w - pad, y)],
                   fill=(128, 128, 128, 50), width=1)

        def draw_line(hist, color):
            data = list(hist)
            n = HISTORY_LEN
            pts = []
            for i, val in enumerate(data):
                x = pad + int(gw * i / (n - 1))
                y = h - pad - int(gh * min(val, 100) / 100)
                pts.append((x, y))
            if len(pts) >= 2:
                d.line(pts, fill=color + (220,), width=3)

        draw_line(self._gpu, GPU_COLOR)
        draw_line(self._cpu, CPU_COLOR)

        # 범례 — 시스템 폰트 20px (=10pt @2x)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        except Exception:
            font = ImageFont.load_default()

        dot = 14  # 동그라미 크기
        dot_y = pad + 2
        d.ellipse([pad, dot_y, pad + dot, dot_y + dot],
                  fill=CPU_COLOR + (230,))
        d.text((pad + dot + 6, dot_y - 1), "CPU", font=font, fill=CPU_COLOR + (230,))
        d.ellipse([pad + 80, dot_y, pad + 80 + dot, dot_y + dot],
                  fill=GPU_COLOR + (230,))
        d.text((pad + 80 + dot + 6, dot_y - 1), "GPU", font=font, fill=GPU_COLOR + (230,))

        # 메모리에서 NSImage 생성 (디스크 I/O 제거)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        data = NSData.dataWithBytes_length_(buffer.getvalue(), len(buffer.getvalue()))
        ns_img = NSImage.alloc().initWithData_(data)
        ns_img.setSize_(NSSize(GRAPH_W, GRAPH_H))
        return ns_img


# ── 메뉴 텍스트 컬러 헬퍼 ─────────────────────────────────────────────
def _make_astr(title, bold=False, font_size=13, dark=True, mono=False):
    from AppKit import NSColor
    if mono:
        font = NSFont.fontWithName_size_("Menlo-Bold" if bold else "Menlo-Regular", font_size)
        if font is None:
            font = NSFont.monospacedSystemFontOfSize_weight_(font_size, 0.4 if bold else 0.0)
    else:
        weight = NSFont.boldSystemFontOfSize_ if bold else NSFont.systemFontOfSize_
        font = weight(font_size)
    color = NSColor.colorWithRed_green_blue_alpha_(0.05, 0.05, 0.05, 1.0) if dark else NSColor.secondaryLabelColor()
    attrs = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: color,
    }
    return NSAttributedString.alloc().initWithString_attributes_(title, attrs)


def set_title(item, title, bold=False, font_size=13, sub=False, mono=False):
    astr = _make_astr(title, bold=bold, font_size=font_size, dark=not sub, mono=mono)
    item._menuitem.setAttributedTitle_(astr)


def make_bar(pct, width=8):
    filled = round(min(pct, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ── 메인 앱 ──────────────────────────────────────────────────────────
class MonitorApp(rumps.App):
    def __init__(self):
        super().__init__("", quit_button=None)

        self._cpu  = 0.0
        self._gpu  = {"device": 0, "renderer": 0, "tiler": 0}
        self._mem  = 0.0
        self._disk = {"used_gb": 0.0, "total_gb": 0.0, "pct": 0.0}
        self._bat  = None
        self._net  = {"up_kb": 0.0, "down_kb": 0.0, "iface": ""}
        self._ollama_proc = None
        self._ollama_api  = None
        self._frame = 0
        self._next_frame_at = 0.0
        self._lock = threading.Lock()
        self._tracker = OllamaProcessTracker()
        self._graph   = HistoryGraph()

        self._stats_counter = 0

        # 그래프용 NSMenuItem (이미지 표시)
        self._graph_nsitem = NSMenuItem.alloc().init()
        self._graph_nsitem.setTitle_("")
        self._graph_nsitem.setEnabled_(False)
        self._graph_inserted = False

        self._item_cpu      = rumps.MenuItem("")
        self._item_gpu      = rumps.MenuItem("")
        self._item_gpu_sub  = rumps.MenuItem("")
        self._item_mem      = rumps.MenuItem("")
        self._item_disk     = rumps.MenuItem("")
        self._item_disk_sub = rumps.MenuItem("")
        self._item_bat      = rumps.MenuItem("")
        self._item_bat_sub  = rumps.MenuItem("")
        self._item_net      = rumps.MenuItem("")
        self._item_net_sub  = rumps.MenuItem("")
        self._item_ol_model = rumps.MenuItem("")
        self._item_ol_vram  = rumps.MenuItem("")
        self._item_ol_cpu   = rumps.MenuItem("")

        self.menu = [
            self._item_cpu,
            self._item_gpu,
            self._item_gpu_sub,
            rumps.separator,
            self._item_mem,
            rumps.separator,
            self._item_disk,
            self._item_disk_sub,
            rumps.separator,
            self._item_bat,
            self._item_bat_sub,
            rumps.separator,
            self._item_net,
            self._item_net_sub,
            rumps.separator,
            self._item_ol_model,
            self._item_ol_vram,
            self._item_ol_cpu,
            rumps.separator,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        self._start_timer(self._collect_stats, 1)
        self._start_timer(self._animate, 0.02)
        rumps.Timer(self._collect_disk, 30).start()
        threading.Thread(target=self._collect_disk, args=(None,), daemon=True).start()

    def _start_timer(self, callback, interval):
        """메뉴가 열려 있어도 타이머가 계속 동작하도록 CommonModes로 등록."""
        t = rumps.Timer(callback, interval)
        t.start()
        NSRunLoop.currentRunLoop().addTimer_forMode_(
            t._nstimer, NSRunLoopCommonModes
        )

    def _insert_graph(self):
        if self._graph_inserted:
            return
        ns_menu = self._item_cpu._menuitem.menu()
        if ns_menu is None:
            return
        idx = ns_menu.indexOfItem_(self._item_cpu._menuitem)
        ns_menu.insertItem_atIndex_(self._graph_nsitem, idx + 1)
        self._graph_inserted = True

    def _collect_disk(self, _):
        disk = get_disk_stats()
        with self._lock:
            self._disk = disk

    def _collect_stats(self, _):
        self._stats_counter += 1
        
        cpu  = psutil.cpu_percent(interval=None)
        mem  = psutil.virtual_memory().percent
        gpu  = get_gpu_stats()
        net  = get_network_stats()
        
        # Ollama 및 배터리는 3초에 한 번만 갱신 (네트워크/부하 경감)
        if self._stats_counter % 3 == 0:
            bat  = get_battery_stats()
            self._tracker.update()
            ol_proc = self._tracker.stats()
            ol_api  = get_ollama_api_info()
            
            with self._lock:
                self._bat         = bat
                self._ollama_proc = ol_proc
                self._ollama_api  = ol_api
        else:
            self._tracker.update() # 생존 확인은 매초 수행 (가벼움)
            ol_proc = self._tracker.stats()
            with self._lock:
                self._ollama_proc = ol_proc

        with self._lock:
            self._cpu         = cpu
            self._mem         = mem
            self._gpu         = gpu
            self._net         = net

        self._graph.push(cpu, gpu["device"])
        ns_img = self._graph.render_nsimage()
        self._graph_nsitem.setImage_(ns_img)
        self._update_menu()

    def _animate(self, _):
        now = time.time()
        with self._lock:
            cpu     = self._cpu
            gpu_pct = self._gpu["device"]
        load = max(cpu, gpu_pct)
        interval = animation_interval(load)
        if now >= self._next_frame_at:
            self._frame = (self._frame + 1) % len(FRAME_PATHS)
            self._next_frame_at = now + interval
            self._set_icon(FRAME_PATHS[self._frame])
        self._insert_graph()

    def _update_menu(self):
        with self._lock:
            cpu     = self._cpu
            mem     = self._mem
            gpu     = self._gpu
            disk    = self._disk
            bat     = self._bat
            net     = self._net
            ol_proc = self._ollama_proc
            ol_api  = self._ollama_api

        LBL = 9  # 레이블 고정 너비 (고정폭 폰트 기준)

        set_title(self._item_cpu,
            f"{'CPU':<{LBL}}{make_bar(cpu)}  {cpu:.1f}%", bold=True, mono=True)
        set_title(self._item_gpu,
            f"{'GPU':<{LBL}}{make_bar(gpu['device'])}  {gpu['device']}%", bold=True, mono=True)
        set_title(self._item_gpu_sub,
            f"  Renderer {gpu['renderer']}%   Tiler {gpu['tiler']}%",
            font_size=11, sub=True)
        set_title(self._item_mem,
            f"{'Memory':<{LBL}}{make_bar(mem)}  {mem:.1f}%", bold=True, mono=True)

        # 저장용량
        set_title(self._item_disk,
            f"{'Disk':<{LBL}}{make_bar(disk['pct'])}  {disk['pct']:.1f}%", bold=True, mono=True)
        set_title(self._item_disk_sub,
            f"  {disk['used_gb']:.1f} GB / {disk['total_gb']:.1f} GB 사용 중",
            font_size=11, sub=True)

        # 배터리
        if bat:
            plug = "⚡ " if bat["plugged"] else ""
            set_title(self._item_bat,
                f"{'Battery':<{LBL}}{make_bar(bat['pct'])}  {plug}{bat['pct']:.0f}%",
                bold=True, mono=True)
            set_title(self._item_bat_sub,
                f"  {bat['remain']}", font_size=11, sub=True)
        else:
            set_title(self._item_bat, f"{'Battery':<{LBL}}N/A", bold=True, mono=True)
            set_title(self._item_bat_sub, "", font_size=11, sub=True)

        # 네트워크
        def fmt_kb(kb):
            return f"{kb/1024:.1f} MB/s" if kb >= 1024 else f"{kb:.0f} KB/s"

        iface = f" ({net['iface']})" if net["iface"] else ""
        set_title(self._item_net,
            f"{'Network':<{LBL}}↑ {fmt_kb(net['up_kb'])}  ↓ {fmt_kb(net['down_kb'])}{iface}",
            bold=True, mono=True)
        set_title(self._item_net_sub, "", font_size=11, sub=True)

        if ol_api:
            set_title(self._item_ol_model,
                f"Ollama  {ol_api['name']}  (ctx {ol_api['context']:,})",
                bold=True)
            set_title(self._item_ol_vram,
                f"  VRAM {ol_api['vram_gb']:.1f} GB  /  Model {ol_api['size_gb']:.1f} GB",
                font_size=11, sub=True)
        else:
            set_title(self._item_ol_model, "Ollama: not running", sub=True)
            set_title(self._item_ol_vram, "  --", font_size=11, sub=True)

        if ol_proc:
            set_title(self._item_ol_cpu,
                f"  CPU {ol_proc['total_cpu']:.1f}%   MEM {ol_proc['mem_mb']:.0f} MB",
                font_size=11, sub=True)
        else:
            set_title(self._item_ol_cpu, "  --", font_size=11, sub=True)

    def _set_icon(self, path):
        img  = NSImage.alloc().initWithContentsOfFile_(path)
        orig = img.size()
        h    = 16.0
        w    = h * orig.width / orig.height
        img.setSize_(NSSize(w, h))
        img.setTemplate_(True)
        self._nsapp.nsstatusitem.setImage_(img)

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    MonitorApp().run()
