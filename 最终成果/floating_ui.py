# -*- coding: utf-8 -*-
"""WorkBuddy 悬浮窗的界面层。

对外只暴露 :func:`run_app`：接收一个无参快照函数，阻塞运行 tkinter 主循环。
仅依赖标准库 tkinter，不读取任何文件，所有数据都来自 snapshot_provider。
"""

import json
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

try:  # 版本号缺失时不影响界面启动
    from version import __version__ as _APP_VERSION
except Exception:      # pragma: no cover - 仅在单独拷贝本文件时触发
    _APP_VERSION = "unknown"

# ------------------------------------------------------------------ 配置存储
def _base_dir():
    """打包成 exe 后 __file__ 指向临时目录，必须用 exe 自身所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_base_dir(), "设置.json")

#: 第一行命中率的口径
SCOPE_OPTIONS = [
    ("session", "当前会话"),
    ("history", "全历史"),
]

#: 第三行可显示的指标
LINE3_OPTIONS = [
    ("session_out", "会话输出"),
    ("history_out", "全历史输出"),
    ("context", "上下文用量"),
    ("requests", "请求次数"),
    ("duration", "会话时长"),
    ("model", "模型名称"),
    ("miss_tokens", "未命中 token"),
    ("none", "隐藏"),
]

#: 刷新间隔候选（毫秒）
REFRESH_OPTIONS = [500, 1000, 2000, 5000]

#: 透明度候选
OPACITY_OPTIONS = [1.0, 0.95, 0.9, 0.85, 0.8]

DEFAULT_CONFIG = {
    "hit_rate_scope": "session",
    "line3": "session_out",
    "refresh_ms": 1000,
    "opacity": 1.0,
    # 窗口上次位置；None 表示还没有记忆过，启动时回落到右下角
    "x": None,
    "y": None,
}


def _load_config():
    """读取 设置.json，任何异常都退回默认值。"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            for key in DEFAULT_CONFIG:
                if key in data:
                    cfg[key] = data[key]
    except Exception:
        return cfg

    if cfg.get("hit_rate_scope") not in [k for k, _ in SCOPE_OPTIONS]:
        cfg["hit_rate_scope"] = DEFAULT_CONFIG["hit_rate_scope"]
    if cfg.get("line3") not in [k for k, _ in LINE3_OPTIONS]:
        cfg["line3"] = DEFAULT_CONFIG["line3"]
    try:
        cfg["refresh_ms"] = int(cfg["refresh_ms"])
    except (TypeError, ValueError):
        cfg["refresh_ms"] = DEFAULT_CONFIG["refresh_ms"]
    if cfg["refresh_ms"] not in REFRESH_OPTIONS:
        cfg["refresh_ms"] = DEFAULT_CONFIG["refresh_ms"]
    try:
        cfg["opacity"] = float(cfg["opacity"])
    except (TypeError, ValueError):
        cfg["opacity"] = DEFAULT_CONFIG["opacity"]
    if cfg["opacity"] not in OPACITY_OPTIONS:
        cfg["opacity"] = DEFAULT_CONFIG["opacity"]
    # 旧版 设置.json 没有 x/y，缺失或非法时统一按“未记忆”处理
    for axis in ("x", "y"):
        value = cfg.get(axis)
        if value is None or isinstance(value, bool):
            cfg[axis] = None
            continue
        try:
            cfg[axis] = int(value)
        except (TypeError, ValueError):
            cfg[axis] = None
    return cfg


def _save_config(cfg):
    """写入 设置.json，失败返回 False（不影响主功能）。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _label_of(options, key):
    for item_key, item_label in options:
        if item_key == key:
            return item_label
    return options[0][1]


# ------------------------------------------------------------------ 尺寸与配色
WIDTH = 268
HEIGHT = 116
RADIUS = 14                      # 圆角半径
MARGIN_RIGHT = 24                # 距屏幕右边缘
MARGIN_BOTTOM = 80               # 距屏幕下边缘
MIN_VISIBLE_W = 60               # 拖动时至少留在屏幕内的宽度
MIN_VISIBLE_H = 40               # 拖动时至少留在屏幕内的高度
HINT_TEXT = "右键设置 · 按住拖动"
HINT_MS = 20000                  # 首次运行的提示停留时长

BG = "#1e1f22"                   # 窗口底色
BORDER = "#32353b"               # 1px 描边，避免深色背景在深色壁纸上糊成一片
FG = "#e6e6e6"                   # 主文字
MUTED = "#8b8f96"                # 次要文字
ACCENT_HIGH = "#4ade80"          # 命中率 >= 60%
ACCENT_MID = "#fbbf24"           # 30% ~ 60%
ACCENT_LOW = "#f87171"           # < 30%
TRANSPARENT_KEY = "#010203"      # Windows 抠色键，用于把直角变成圆角

# 文本锚点坐标（Canvas 内的像素，窗口左上角为原点）
X_TEXT = 18
Y_LINE1 = 32
Y_LINE2 = 66
Y_LINE3 = 92

FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Tahoma")


# ------------------------------------------------------------------ 数值格式化
def _as_float(value):
    """尽力把快照里的值转成 float，拿不到就返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_percent(value):
    """0.0~1.0 的命中率 -> "72.5%"，None -> "--"。"""
    rate = _as_float(value)
    if rate is None:
        return "--"
    return "{:.1f}%".format(rate * 100.0)


def _fmt_speed(value):
    """tok/s -> "38.4 tok/s"，None -> "-- tok/s"。"""
    speed = _as_float(value)
    if speed is None:
        return "-- tok/s"
    return "{:.1f} tok/s".format(speed)


def _fmt_tokens(value):
    """累计输出 token -> "22.3M" / "18.4K" / "860"，None -> "--"。"""
    total = _as_int(value)
    if total is None:
        return "--"
    if total >= 1000000:
        return "{:.1f}M".format(total / 1000000.0)
    if total >= 1000:
        return "{:.1f}K".format(total / 1000.0)
    return str(total)


def _fmt_duration(seconds):
    """秒数 -> "45 秒" / "12 分" / "6.7 时" / "--"。"""
    value = _as_float(seconds)
    if value is None or value <= 0:
        return "--"
    if value < 60:
        return "{:.0f} 秒".format(value)
    if value < 3600:
        return "{:.0f} 分".format(value / 60.0)
    return "{:.1f} 时".format(value / 3600.0)


def _line3_text(metric, snapshot):
    """按配置生成第三行文本。"""
    if metric == "none":
        return ""
    if metric == "session_out":
        return "会话 {} tok · {} 条".format(
            _fmt_tokens(snapshot.get("total_out")),
            _as_int(snapshot.get("records")) or 0,
        )
    if metric == "history_out":
        if not snapshot.get("history_ready"):
            return "历史统计中…"
        return "历史 {} tok · {} 条".format(
            _fmt_tokens(snapshot.get("history_out")),
            _as_int(snapshot.get("history_records")) or 0,
        )
    if metric == "context":
        return "上下文 {} tok".format(_fmt_tokens(snapshot.get("context_tokens")))
    if metric == "requests":
        return "请求 {} 次".format(_as_int(snapshot.get("records")) or 0)
    if metric == "duration":
        return "时长 {}".format(_fmt_duration(snapshot.get("session_duration")))
    if metric == "model":
        name = snapshot.get("model") or "--"
        return "模型 {}".format(name)
    if metric == "miss_tokens":
        return "未命中 {} tok".format(_fmt_tokens(snapshot.get("miss_tokens")))
    return ""


def _accent_for(value):
    """命中率对应的强调色；无数据时退回次要色。"""
    rate = _as_float(value)
    if rate is None:
        return MUTED
    if rate >= 0.60:
        return ACCENT_HIGH
    if rate >= 0.30:
        return ACCENT_MID
    return ACCENT_LOW


def _clip(text, limit=30):
    """把异常信息压成一行短文本，避免撑破窗口。"""
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _pick_font_family(root):
    """优先 Microsoft YaHei UI，逐级回退。"""
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    for name in FONT_CANDIDATES:
        if name in available:
            return name
    return "TkDefaultFont"


# ------------------------------------------------------------------ 窗口实现
class _FloatingWindow(object):
    def __init__(self, root, snapshot_provider, interval_ms):
        self.root = root
        self.provider = snapshot_provider
        # 配置文件不存在即视为首次运行，用于显示“右键设置”的引导
        self._first_run = not os.path.exists(CONFIG_PATH)
        self.cfg = _load_config()
        # 配置中的刷新间隔优先于构造参数，保证设置界面改完即生效
        try:
            configured = int(self.cfg.get("refresh_ms") or 0)
        except (TypeError, ValueError):
            configured = 0
        self.interval_ms = max(200, configured or int(interval_ms))
        self._alive = True
        self._last = {}
        self._last_error = None
        self._drag_dx = 0
        self._drag_dy = 0
        self._after_id = None
        self._hint_after = None
        self._hint_active = self._first_run

        self.family = _pick_font_family(root)

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        try:
            root.attributes("-alpha", float(self.cfg.get("opacity", 1.0)))
        except (tk.TclError, TypeError, ValueError):
            pass

        # 圆角：Windows 上把窗口底色抠成透明，露出 Canvas 画出来的圆角矩形
        self._rounded = False
        try:
            root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
            self._rounded = True
        except tk.TclError:
            self._rounded = False

        key = TRANSPARENT_KEY if self._rounded else BG
        self.canvas = tk.Canvas(
            root,
            width=WIDTH,
            height=HEIGHT,
            bg=key,
            highlightthickness=0,
            bd=0,
            takefocus=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._draw_background()
        self._build_text_items()
        self._build_menu()
        self._bind_events()
        self._place_initial()

        # 先把窗口画出来再加载数据：首次全量扫描约 2.5 秒，若直接调用
        # _refresh()，用户会在这段时间里看不到任何窗口。update() 之后
        # 窗口已可见（显示 "--" 占位），随后再填充真实数值。
        try:
            self.root.update_idletasks()
            self.root.update()
        except tk.TclError:
            pass

        self._refresh()
        self._schedule()
        if self._hint_active:
            try:
                self._hint_after = self.root.after(HINT_MS, self._clear_hint)
            except tk.TclError:
                self._hint_after = None
                self._hint_active = False

    # ---------------------------------------------------------- 静态绘制
    def _draw_background(self):
        r = RADIUS
        x1, y1 = 0.5, 0.5
        x2, y2 = WIDTH - 0.5, HEIGHT - 0.5
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1,
            x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2,
            x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.canvas.create_polygon(
            points,
            smooth=True,
            splinesteps=32,
            fill=BG,
            outline=BORDER,
            width=1,
        )

    def _build_text_items(self):
        # 第一行：命中率，大号 + 强调色
        self.item_rate = self.canvas.create_text(
            X_TEXT, Y_LINE1,
            text="命中率 --",
            anchor="w",
            fill=MUTED,
            font=(self.family, 18, "bold"),
        )
        # 第二行：速率，中号
        self.item_speed = self.canvas.create_text(
            X_TEXT, Y_LINE2,
            text="-- tok/s",
            anchor="w",
            fill=FG,
            font=(self.family, 13),
        )
        # 第三行：累计输出 + 文件数，小字次要色
        self.item_total = self.canvas.create_text(
            X_TEXT, Y_LINE3,
            text="会话 -- tok · -- 条",
            anchor="w",
            fill=MUTED,
            font=(self.family, 9),
        )

    def _build_menu(self):
        self.menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=BG,
            fg=FG,
            activebackground="#32353b",
            activeforeground=FG,
            bd=0,
            relief="flat",
            font=(self.family, 9),
        )
        self.menu.add_command(label="刷新", command=self._refresh)
        self.menu.add_command(label="设置…", command=self._open_settings)
        self.menu.add_command(label="复制快照", command=self._copy_snapshot)
        self.menu.add_command(label="打开程序目录", command=self._open_base_dir)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)

    def _bind_events(self):
        for widget in (self.canvas, self.root):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._on_release)
            widget.bind("<Button-3>", self._on_menu)

    # ---------------------------------------------------------- 位置
    def _screen_bounds(self):
        """返回整个桌面矩形 (left, top, right, bottom)。

        优先用虚拟桌面（覆盖所有显示器，可带负坐标），拿不到时退回主屏。
        """
        try:
            left = int(self.root.winfo_vrootx())
            top = int(self.root.winfo_vrooty())
            width = int(self.root.winfo_vrootwidth())
            height = int(self.root.winfo_vrootheight())
        except (tk.TclError, ValueError):
            left = top = 0
            width, height = 0, 0
        if width <= 0 or height <= 0:
            left, top = 0, 0
            width = int(self.root.winfo_screenwidth())
            height = int(self.root.winfo_screenheight())
        if width <= 0 or height <= 0:
            # 极端情况下拿不到屏幕尺寸，给一个保守的默认值避免除零/死循环
            width, height = 1024, 768
        return left, top, left + width, top + height

    def _clamp_position(self, x, y):
        """把窗口坐标钳到「至少 MIN_VISIBLE_W x MIN_VISIBLE_H 可见」。"""
        left, top, right, bottom = self._screen_bounds()
        min_x = left - WIDTH + MIN_VISIBLE_W
        max_x = right - MIN_VISIBLE_W
        min_y = top - HEIGHT + MIN_VISIBLE_H
        max_y = bottom - MIN_VISIBLE_H
        x = min(max(int(x), min_x), max_x)
        y = min(max(int(y), min_y), max_y)
        return x, y

    def _place_initial(self):
        """优先用上次记住的位置，与桌面无交集时回落到右下角。"""
        x = self.cfg.get("x")
        y = self.cfg.get("y")
        if x is not None and y is not None and self._intersects_screen(x, y):
            self.root.geometry("{}x{}+{}+{}".format(WIDTH, HEIGHT, int(x), int(y)))
            return
        self._place_bottom_right()

    def _intersects_screen(self, x, y):
        """窗口与桌面矩形是否有交集（负坐标合法，多显示器也成立）。"""
        left, top, right, bottom = self._screen_bounds()
        return x + WIDTH > left and y + HEIGHT > top and x < right and y < bottom

    def _place_bottom_right(self):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, screen_w - WIDTH - MARGIN_RIGHT)
        y = max(0, screen_h - HEIGHT - MARGIN_BOTTOM)
        # 多显示器 / 缩放比例不一致时，算出的位置可能落到可见区域之外，
        # 表现为「双击后看不到窗口」。越界就退回左上角安全位置。
        if screen_w > 0 and (x + WIDTH > screen_w or x < 0):
            x = 80
        if screen_h > 0 and (y + HEIGHT > screen_h or y < 0):
            y = 80
        self.root.geometry("{}x{}+{}+{}".format(WIDTH, HEIGHT, x, y))

    # ---------------------------------------------------------- 拖动
    def _on_press(self, event):
        self._drag_dx = event.x_root - self.root.winfo_x()
        self._drag_dy = event.y_root - self.root.winfo_y()

    def _on_drag(self, event):
        if not self._alive:
            return
        x, y = self._clamp_position(
            event.x_root - self._drag_dx,
            event.y_root - self._drag_dy,
        )
        self.root.geometry("+{}+{}".format(x, y))

    def _on_release(self, event):
        """松开鼠标后记住位置；写盘失败不影响拖动，故忽略返回值。"""
        if not self._alive:
            return
        try:
            x = int(self.root.winfo_x())
            y = int(self.root.winfo_y())
        except (tk.TclError, ValueError):
            return
        if self.cfg.get("x") == x and self.cfg.get("y") == y:
            return
        self.cfg["x"] = x
        self.cfg["y"] = y
        _save_config(self.cfg)

    # ---------------------------------------------------------- 右键菜单
    def _on_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.menu.grab_release()
            except tk.TclError:
                pass

    # ---------------------------------------------------------- 设置窗口
    def _apply_config(self):
        """把配置应用到窗口：透明度、刷新间隔、立即重绘。"""
        try:
            self.root.attributes("-alpha", float(self.cfg.get("opacity", 1.0)))
        except (tk.TclError, TypeError, ValueError):
            pass
        try:
            self.interval_ms = max(200, int(self.cfg.get("refresh_ms") or 1000))
        except (TypeError, ValueError):
            self.interval_ms = 1000
        self._schedule()
        self._refresh()

    def _open_settings(self):
        """打开设置窗口；已打开则聚焦，避免重复创建。"""
        existing = getattr(self, "_settings_win", None)
        if existing is not None:
            try:
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return
            except tk.TclError:
                self._settings_win = None

        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("悬浮窗设置 · v%s" % _APP_VERSION)
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.geometry(
            "352x372+%d+%d"
            % (
                max(0, self.root.winfo_x() - 20),
                max(0, self.root.winfo_y() - 400),
            )
        )

        def style_option(menu_widget):
            menu_widget.configure(
                bg="#2b2d31", fg=FG,
                activebackground="#32353b", activeforeground=FG,
                bd=0, highlightthickness=0, relief="flat",
                font=(self.family, 9), width=12, anchor="w",
            )
            menu_widget["menu"].configure(
                bg=BG, fg=FG,
                activebackground="#32353b", activeforeground=FG,
                font=(self.family, 9),
            )

        head = tk.Label(
            win, text="显示设置", bg=BG, fg=FG, font=(self.family, 11, "bold")
        )
        head.grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 10))

        rows = [
            ("命中率口径", SCOPE_OPTIONS, "hit_rate_scope"),
            ("第三行显示", LINE3_OPTIONS, "line3"),
            ("刷新间隔", [(str(v), "%g 秒" % (v / 1000.0)) for v in REFRESH_OPTIONS], "refresh_ms"),
            ("窗口透明度", [(str(v), "%d%%" % round(v * 100)) for v in OPACITY_OPTIONS], "opacity"),
        ]

        variables = {}
        for index, (label, options, key) in enumerate(rows, start=1):
            tk.Label(
                win, text=label, bg=BG, fg=MUTED, font=(self.family, 9)
            ).grid(row=index, column=0, sticky="w", padx=(16, 8), pady=5)

            current = self.cfg.get(key)
            current_label = _label_of(options, str(current))
            var = tk.StringVar(value=current_label)
            variables[key] = (var, options)
            menu_widget = tk.OptionMenu(win, var, *[lbl for _, lbl in options])
            style_option(menu_widget)
            menu_widget.grid(row=index, column=1, sticky="e", padx=(0, 16), pady=5)

        def close():
            self._settings_win = None
            try:
                win.destroy()
            except tk.TclError:
                pass

        # 保存失败时在窗口底部提示，不关窗，让用户知道设置没有落盘
        msg_var = tk.StringVar(value="")

        def do_save():
            for key, (var, options) in variables.items():
                chosen = var.get()
                for opt_key, opt_label in options:
                    if opt_label == chosen:
                        if key == "refresh_ms":
                            self.cfg[key] = int(opt_key)
                        elif key == "opacity":
                            self.cfg[key] = float(opt_key)
                        else:
                            self.cfg[key] = opt_key
                        break
            if not _save_config(self.cfg):
                msg_var.set("保存失败：程序目录不可写，本次设置仅本次有效")
                self._apply_config()
                return
            self._apply_config()
            close()

        def button(parent, text, command, primary=False):
            return tk.Button(
                parent, text=text, command=command,
                bg="#2f6f4f" if primary else "#2b2d31",
                fg="#ffffff" if primary else FG,
                activebackground="#3a8560" if primary else "#32353b",
                activeforeground=FG,
                bd=0, relief="flat", highlightthickness=0,
                font=(self.family, 9), width=8, cursor="hand2",
            )

        bar = tk.Frame(win, bg=BG)
        bar.grid(row=len(rows) + 1, column=0, columnspan=2, pady=(16, 6))
        button(bar, "保存", do_save, primary=True).pack(side="right", padx=(8, 0))
        button(bar, "取消", close).pack(side="right")

        tk.Label(
            win,
            textvariable=msg_var,
            bg=BG,
            fg=ACCENT_LOW,
            font=(self.family, 9),
            wraplength=320,
            justify="left",
        ).grid(row=len(rows) + 2, column=0, columnspan=2, padx=16, pady=(0, 12))

        win.protocol("WM_DELETE_WINDOW", close)

        # 键盘路径：Esc 取消，Enter 保存（主窗口无焦点，不绑全局键）
        def on_escape(_event):
            close()
            return "break"

        def on_return(_event):
            do_save()
            return "break"

        win.bind("<Escape>", on_escape)
        win.bind("<Return>", on_return)
        win.bind("<KP_Enter>", on_return)
        try:
            win.focus_force()
        except tk.TclError:
            pass

    # ---------------------------------------------------------- 数据刷新
    def _schedule(self):
        """只保留一条 after 链，避免手动刷新叠加出多个定时器。"""
        if not self._alive:
            return
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        try:
            self._after_id = self.root.after(self.interval_ms, self._tick)
        except tk.TclError:
            self._after_id = None

    def _tick(self):
        if not self._alive:
            return
        self._refresh()
        self._schedule()

    def _refresh(self):
        if not self._alive:
            return
        snapshot = {}
        error = None
        try:
            raw = self.provider()
            if isinstance(raw, dict):
                snapshot = raw
                error = raw.get("error")
            else:
                error = "快照格式异常"
        except Exception as exc:                      # provider 任何异常都不能中断循环
            error = exc or "provider 调用失败"

        self._last = snapshot
        self._last_error = error
        self._render(snapshot, error)

    def _render(self, snapshot, error):
        if error:
            message = _clip(error, 40) or "数据读取异常"
            self.canvas.itemconfigure(self.item_rate, text="数据读取异常", fill=ACCENT_LOW)
            self.canvas.itemconfigure(self.item_speed, text="命中率 -- · 速率 --", fill=MUTED)
            self.canvas.itemconfigure(self.item_total, text=message, fill=MUTED)
            return

        # 第一行：按设置选用会话口径或历史口径
        scope = self.cfg.get("hit_rate_scope")
        if scope == "history":
            rate = snapshot.get("history_hit_rate")
            prefix = "全历史 "
        else:
            rate = snapshot.get("hit_rate")
            prefix = "命中率 "

        # 数据层可能暂时还没提供 speed_idle，用 .get 兜底，缺省视为“还没算出来”
        records = _as_int(snapshot.get("records"))
        idle = bool(snapshot.get("speed_idle"))
        if scope != "history" and records == 0 and not idle:
            # 首轮扫描尚未出数据，先给出状态而不是干巴巴的 "--"
            self.canvas.itemconfigure(
                self.item_rate, text="正在扫描…", fill=MUTED
            )
        else:
            self.canvas.itemconfigure(
                self.item_rate,
                text=prefix + _fmt_percent(rate),
                fill=_accent_for(rate),
            )

        speed = snapshot.get("speed")
        speed_text = "空闲" if (speed is None and idle) else _fmt_speed(speed)
        self.canvas.itemconfigure(
            self.item_speed,
            text=speed_text,
            fill=FG,
        )
        # 第三行：首次运行先显示操作引导，到时自动恢复真实指标
        if self._hint_active:
            self.canvas.itemconfigure(self.item_total, text=HINT_TEXT, fill=MUTED)
            return
        # 第三行：按设置切换指标
        self.canvas.itemconfigure(
            self.item_total,
            text=_line3_text(self.cfg.get("line3", "session_out"), snapshot),
            fill=MUTED,
        )

    def _clear_hint(self):
        """首次运行提示到期，恢复第三行的真实指标。"""
        self._hint_after = None
        self._hint_active = False
        self._render(self._last, self._last_error)

    # ---------------------------------------------------------- 复制与退出
    def _snapshot_text(self):
        snap = self._last or {}
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        lines = ["WorkBuddy 用量快照 v%s  %s" % (_APP_VERSION, stamp)]

        if self._last_error:
            lines.append("状态: 数据读取异常")
            lines.append("异常: " + str(self._last_error))
            return "\n".join(lines)

        lines.append("命中率: " + _fmt_percent(snap.get("hit_rate")))
        lines.append("速率: " + _fmt_speed(snap.get("speed")))
        lines.append("累计输出: " + _fmt_tokens(snap.get("total_out")))
        lines.append(
            "命中/未命中 token: {} / {}".format(
                _as_int(snap.get("hit_tokens")) or 0,
                _as_int(snap.get("miss_tokens")) or 0,
            )
        )
        lines.append("记录数: {}".format(_as_int(snap.get("records")) or 0))
        lines.append("监控文件: {}".format(_as_int(snap.get("files")) or 0))
        active = snap.get("active_file")
        if active:
            lines.append("活跃文件: " + str(active))
        return "\n".join(lines)

    def _copy_snapshot(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._snapshot_text())
        except tk.TclError:
            pass

    def _open_base_dir(self):
        """打开程序所在目录（设置.json / 运行日志.txt 都在这里）。"""
        try:
            os.startfile(_base_dir())
        except Exception:
            pass

    def quit(self):
        self._alive = False
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._hint_after is not None:
            try:
                self.root.after_cancel(self._hint_after)
            except tk.TclError:
                pass
            self._hint_after = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass


# ------------------------------------------------------------------ 对外入口
def run_app(snapshot_provider, interval_ms=1000):
    """阻塞式运行悬浮窗。

    snapshot_provider: 无参 callable，返回契约约定的快照 dict。
    interval_ms: 刷新间隔（毫秒）。
    """
    root = tk.Tk()
    root.title("WorkBuddy 用量 v%s" % _APP_VERSION)
    _FloatingWindow(root, snapshot_provider, interval_ms)
    root.mainloop()


if __name__ == "__main__":
    # 直接运行本文件时用一个假数据源预览界面
    demo = {
        "hit_rate": 0.725,
        "speed": 38.4,
        "total_out": 18400,
        "hit_tokens": 15104,
        "miss_tokens": 5732,
        "records": 95,
        "files": 8,
        "active_file": "demo.jsonl",
        "updated_at": time.time(),
        "error": None,
    }
    run_app(lambda: demo, interval_ms=1000)
