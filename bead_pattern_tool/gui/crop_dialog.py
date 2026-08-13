import os, tkinter as tk, atexit
from tkinter import ttk
from PIL import Image, ImageTk

# ── 主题配色 ──────────────────────────────
BG          = "#1a1a28"
BG2         = "#222236"
ACCENT      = "#7C83FF"
ACCENT_HI   = "#9CA0FF"
GOLD        = "#FFCD38"
GOLD_DIM    = "#B8960A"
TEXT        = "#D2D2E0"
TEXT_MUTED  = "#7A7A8C"
OVERLAY     = (26, 26, 40, 180)   # RGBA 遮罩色
CANVAS_W    = 640
CANVAS_H    = 640
TOP_BAR_H   = 36

# ── 临时文件追踪与清理 ──────────────────────────
_temp_files = set()


def _register_temp(path):
    """注册临时文件，在进程退出时自动删除"""
    _temp_files.add(path)
    atexit.register(_cleanup_temp, path)


def _cleanup_temp(path):
    """安全删除单个临时文件"""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

# ttk 自定义按钮样式
_STYLE_APPLIED = False


def _apply_style():
    global _STYLE_APPLIED
    if _STYLE_APPLIED:
        return
    _STYLE_APPLIED = True
    s = ttk.Style()
    s.theme_use("clam")
    s.configure("Crop.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                padding=(22, 8), borderwidth=0, relief="flat")
    s.map("Crop.TButton",
          background=[("active", ACCENT), ("!active", BG2)],
          foreground=[("active", "#fff"), ("!active", TEXT)])
    s.configure("Crop.TButton", background=BG2, foreground=TEXT)

    # 确认裁剪主按钮
    s.configure("Crop.Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                padding=(28, 8), borderwidth=0, relief="flat")
    s.map("Crop.Primary.TButton",
          background=[("active", ACCENT_HI), ("!active", ACCENT)],
          foreground=[("active", "#fff"), ("!active", "#fff")])
    s.configure("Crop.Primary.TButton", background=ACCENT, foreground="#fff")


class CropDialog(tk.Toplevel):
    def __init__(self, parent, img_path):
        super().__init__(parent)
        _apply_style()

        self.configure(bg=BG)
        self.title("裁剪图片 · 1:1")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # 设置窗口图标
        try:
            from ..config import ICON_PATH
            if os.path.exists(ICON_PATH) and ICON_PATH.endswith('.ico'):
                self.iconbitmap(default=ICON_PATH)
        except Exception:
            pass

        self.img_path = img_path
        self.result_path = None

        self.src = Image.open(img_path).convert("RGB")
        sw, sh = self.src.size
        self.src_w = sw
        self.src_h = sh

        self.zoom = 1.0
        self.offset_x = 0
        self.offset_y = 0

        self.crop_size = min(sw, sh)
        self.crop_x = (sw - self.crop_size) // 2
        self.crop_y = (sh - self.crop_size) // 2

        self._drag_start = None
        self._drag_type = None

        init_zoom = min(CANVAS_W / max(sw, sh), 1.0)
        self.zoom = init_zoom

        # ── 顶部标题栏 ──
        top = tk.Frame(self, bg=BG2, height=TOP_BAR_H)
        top.pack(fill=tk.X, side=tk.TOP)
        top.pack_propagate(False)
        tk.Label(top, text="拖拽裁剪框选择区域  ·  滚轮缩放  ·  框内拖拽移动",
                 bg=BG2, fg=TEXT_MUTED, font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=14)

        # ── 画布区域 ──
        canvas_frame = tk.Frame(self, bg=BG, padx=12, pady=12)
        canvas_frame.pack()

        self.canvas = tk.Canvas(canvas_frame, width=CANVAS_W, height=CANVAS_H,
                                bg="#141420", highlightthickness=1,
                                highlightbackground="#2e2e44")
        self.canvas.pack()

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_wheel)

        # ── 信息栏（尺寸） ──
        info = tk.Frame(self, bg=BG)
        info.pack(fill=tk.X, padx=16)
        self._size_label = tk.Label(info, text="", bg=BG, fg=TEXT_MUTED,
                                    font=("Consolas", 9))
        self._size_label.pack(side=tk.LEFT)

        # ── 按钮栏 ──
        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=(6, 14))
        ttk.Button(btn_frame, text="跳过裁剪", style="Crop.TButton",
                   command=self._skip).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="确认裁剪", style="Crop.Primary.TButton",
                   command=self._confirm).pack(side=tk.LEFT)

        self._redraw()

        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        px = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        self.geometry(f"+{px}+{py}")

    # ── 绘制 ──────────────────────────────────────
    def _redraw(self):
        c = self.canvas
        c.delete("all")

        z = self.zoom
        ox = self.offset_x
        oy = self.offset_y

        disp_w = int(self.src_w * z)
        disp_h = int(self.src_h * z)
        if disp_w < 1: disp_w = 1
        if disp_h < 1: disp_h = 1

        self._photo_img = self.src.resize((disp_w, disp_h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(self._photo_img)
        c.create_image(ox, oy, anchor=tk.NW, image=self._photo, tags="img")

        # 裁剪框坐标
        cx1 = ox + int(self.crop_x * z)
        cy1 = oy + int(self.crop_y * z)
        cx2 = ox + int((self.crop_x + self.crop_size) * z)
        cy2 = oy + int((self.crop_y + self.crop_size) * z)

        # 遮罩（四块半透明矩形拼出框外暗区）
        mask_alpha = 140
        mask_color = "#%02x%02x%02x" % (12, 12, 24)

        # 上遮罩
        if cy1 > -1000:
            c.create_rectangle(-2000, -2000, 6000, cy1, fill=mask_color,
                               stipple="gray50", outline="", tags="mask")
        # 下遮罩
        if cy2 < 6000:
            c.create_rectangle(-2000, cy2, 6000, 6000, fill=mask_color,
                               stipple="gray50", outline="", tags="mask")
        # 左遮罩
        if cx1 > -1000:
            c.create_rectangle(-2000, cy1, cx1, cy2, fill=mask_color,
                               stipple="gray50", outline="", tags="mask")
        # 右遮罩
        if cx2 < 6000:
            c.create_rectangle(cx2, cy1, 6000, cy2, fill=mask_color,
                               stipple="gray50", outline="", tags="mask")

        # 裁剪框外框（发光外描边）
        c.create_rectangle(cx1 - 4, cy1 - 4, cx2 + 4, cy2 + 4,
                           outline="#3D3D66", width=2, tags="glow")
        c.create_rectangle(cx1 - 2, cy1 - 2, cx2 + 2, cy2 + 2,
                           outline="#5A5A84", width=1, tags="glow2")

        # 主裁剪框
        c.create_rectangle(cx1, cy1, cx2, cy2,
                           outline=GOLD, width=2, tags="crop")

        # 九宫格辅助线
        third_w = (cx2 - cx1) / 3.0
        third_h = (cy2 - cy1) / 3.0
        dash = (4, 6)
        c.create_line(cx1 + third_w, cy1, cx1 + third_w, cy2,
                      fill=GOLD_DIM, width=1, dash=dash, tags="grid")
        c.create_line(cx1 + 2 * third_w, cy1, cx1 + 2 * third_w, cy2,
                      fill=GOLD_DIM, width=1, dash=dash, tags="grid")
        c.create_line(cx1, cy1 + third_h, cx2, cy1 + third_h,
                      fill=GOLD_DIM, width=1, dash=dash, tags="grid")
        c.create_line(cx1, cy1 + 2 * third_h, cx2, cy1 + 2 * third_h,
                      fill=GOLD_DIM, width=1, dash=dash, tags="grid")

        # 四角标识（L 形角标）
        corner_len = min(16, (cx2 - cx1) // 6, (cy2 - cy1) // 6)
        cw = 3
        for _cx, _cy, dx, dy in [
            (cx1, cy1,  1,  1),
            (cx2, cy1, -1,  1),
            (cx1, cy2,  1, -1),
            (cx2, cy2, -1, -1),
        ]:
            c.create_line(_cx, _cy, _cx + dx * corner_len, _cy,
                          fill=GOLD, width=cw, tags="corner")
            c.create_line(_cx, _cy, _cx, _cy + dy * corner_len,
                          fill=GOLD, width=cw, tags="corner")

        # 尺寸标签（裁剪框右下角外侧）
        label_text = f"{self.crop_size} px"
        label_x = cx2 + 6
        label_y = cy2 + 4
        c.create_text(label_x, label_y, text=label_text,
                      anchor=tk.NW, fill=TEXT_MUTED,
                      font=("Consolas", 9), tags="info")

        c.tag_lower("mask", "all")
        c.tag_lower("glow", "all")
        c.tag_lower("glow2", "all")
        c.tag_lower("img", "all")
        c.tag_raise("grid", "all")
        c.tag_raise("crop", "all")
        c.tag_raise("corner", "all")
        c.tag_raise("info", "all")

        # 底部信息栏更新
        self._size_label.config(
            text=f"裁剪范围: {self.crop_size} × {self.crop_size} px")

    def _on_press(self, event):
        z = self.zoom
        ox = self.offset_x
        oy = self.offset_y

        # 裁剪框画布坐标（与 _redraw 同步）
        cx1 = ox + int(self.crop_x * z)
        cy1 = oy + int(self.crop_y * z)
        cx2 = ox + int((self.crop_x + self.crop_size) * z)
        cy2 = oy + int((self.crop_y + self.crop_size) * z)

        edge = 10
        if abs(event.x - cx1) <= edge and cy1 - edge <= event.y <= cy2 + edge:
            self._drag_type = "resize_left"
        elif abs(event.x - cx2) <= edge and cy1 - edge <= event.y <= cy2 + edge:
            self._drag_type = "resize_right"
        elif abs(event.y - cy1) <= edge and cx1 - edge <= event.x <= cx2 + edge:
            self._drag_type = "resize_top"
        elif abs(event.y - cy2) <= edge and cx1 - edge <= event.x <= cx2 + edge:
            self._drag_type = "resize_bottom"
        elif cx1 <= event.x <= cx2 and cy1 <= event.y <= cy2:
            self._drag_type = "move"
        elif (cx1 <= event.x <= cx2 or cy1 <= event.y <= cy2) and \
             abs(event.x - cx1) <= edge or abs(event.x - cx2) <= edge or \
             abs(event.y - cy1) <= edge or abs(event.y - cy2) <= edge:
            self._drag_type = "resize_corner"
        else:
            self._drag_type = "pan"
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event):
        if self._drag_start is None:
            return
        z = self.zoom
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]

        if self._drag_type == "move":
            self.crop_x += int(dx / z)
            self.crop_y += int(dy / z)
            self.crop_x = max(0, min(self.crop_x, self.src_w - self.crop_size))
            self.crop_y = max(0, min(self.crop_y, self.src_h - self.crop_size))
        elif self._drag_type in ("resize_left", "resize_right", "resize_top", "resize_bottom", "resize_corner"):
            new_size = self.crop_size
            if "left" in self._drag_type:
                new_size -= int(dx / z)
            elif "right" in self._drag_type:
                new_size += int(dx / z)
            if "top" in self._drag_type:
                new_size -= int(dy / z)
            elif "bottom" in self._drag_type:
                new_size += int(dy / z)
            if self._drag_type == "resize_corner":
                if dx > 0 or dy > 0:
                    new_size = max(int(min(dx, dy) / z) + self.crop_size, 10)
                else:
                    new_size = max(int(max(dx, dy) / z) + self.crop_size, 10)
            new_size = max(10, min(new_size, self.src_w, self.src_h))
            old_size = self.crop_size
            self.crop_size = new_size
            if "left" in self._drag_type:
                self.crop_x += old_size - new_size
            if "top" in self._drag_type:
                self.crop_y += old_size - new_size
            self.crop_x = max(0, min(self.crop_x, self.src_w - self.crop_size))
            self.crop_y = max(0, min(self.crop_y, self.src_h - self.crop_size))
        elif self._drag_type == "pan":
            self.offset_x += dx
            self.offset_y += dy

        self._drag_start = (event.x, event.y)
        self._redraw()

    def _on_release(self, event):
        self._drag_start = None
        self._drag_type = None

    def _on_wheel(self, event):
        old_zoom = self.zoom
        if event.delta > 0:
            self.zoom = min(5.0, self.zoom * 1.1)
        else:
            self.zoom = max(0.1, self.zoom / 1.1)

        mx = event.x - self.offset_x
        my = event.y - self.offset_y
        self.offset_x -= mx * (self.zoom / old_zoom - 1)
        self.offset_y -= my * (self.zoom / old_zoom - 1)
        self._redraw()

    def _crop_and_save(self):
        box = (self.crop_x, self.crop_y,
               self.crop_x + self.crop_size, self.crop_y + self.crop_size)
        cropped = self.src.crop(box)
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".png", prefix="crop_")
        os.close(fd)
        cropped.save(path, "PNG")
        _register_temp(path)  # 注册退出时自动清理
        return path

    def _confirm(self):
        self.result_path = self._crop_and_save()
        self.destroy()

    def _skip(self):
        self.result_path = self.img_path
        self.destroy()


def show_crop_dialog(parent, img_path):
    dlg = CropDialog(parent, img_path)
    dlg.wait_window()
    return dlg.result_path
