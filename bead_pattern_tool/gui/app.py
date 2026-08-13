#!/usr/bin/env python3
"""夕的画板 — 为游戏社区玩家打造的拼豆图纸生成器"""

import os, sys, tkinter as tk, webbrowser, traceback
from tkinter import filedialog, ttk, messagebox
from collections import Counter
from PIL import Image, ImageTk
from .crop_dialog import show_crop_dialog

# ═══════════════ 高 DPI 感知（必须在 Tk() 创建前声明） ═══════════════
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from ..config import (DEFAULT_N, N_MIN, N_MAX, CELL, SMOOTH_STRENGTH, DOUYIN_URL, ICON_PATH,
                       MODE_CONFIG)
from ..config import MODE_PHOTO, MODE_ILLUSTRATION, MODE_EDGE, MODE_DITHER, MODE_NAMES
from ..core import BeadEngineOptimized, render_pattern
from ..core.palette import build_catalog
from .theme import (apply_theme, BG_PRIMARY, BG_SECONDARY, BG_TERTIARY,
                     TEXT_PRIMARY, TEXT_SECONDARY, ACCENT, FONT_BODY,
                     WARNING, SUCCESS, BG_ACTIVE)
from .widgets import RoundedButton, CardFrame, ModernSlider, GradientFrame

START_TIP = """欢迎使用「夕的画板」！

上传你喜欢的游戏截图或角色图片，工具会把它变成一张拼豆施工图纸。

━━━━ 渲染模式 ━━━━
[照片模式]  线性光面积采样，色彩还原最佳，适合大多数场景
[插画模式]  提取主色并保持区域一致性，适合二次元/Q版图像
[边缘模式]  混合采样保留硬轮廓，适合立绘/带文字图片
[抖动模式]  蛇形 Floyd-Steinberg 误差扩散，适合渐变/大幅面

━━━━ 推荐图片 ━━━━
[推] 角色立绘、Q版形象、像素风作品
[推] 主体清晰、背景简洁的截图
[推] 颜色鲜明的图片效果更好

━━━━ 不太适合的图片 ━━━━
[避] 背景杂乱、主体过小的照片
[避] 颜色过于接近的大场景截图

━━━━ 操作小贴士 ━━━━
- 打开图片后，右键点击图纸中的色块可以取色
- 左键单点换色，按住 Ctrl 拖动可以连续涂抹
- 不喜欢某个像素？用 Ctrl+Z 撤销
- 图纸尺寸越大，需要的拼豆数量越多，建议 20~24 比较适中
- 启用「描边」会自动优先使用边缘模式
"""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        # 高 DPI 缩放适配：匹配系统缩放因子，防字体锯齿和 Canvas 坐标错乱
        if sys.platform == "win32":
            try:
                hwnd = self.winfo_id()
                dpi = ctypes.windll.shcore.GetDpiForWindow(hwnd)
                self.tk.call('tk', 'scaling', dpi / 72.0)
            except Exception:
                pass
        self.title("夕的画板")
        self.geometry("1600x1060"); self.minsize(1200, 820)
        self.configure(bg=BG_PRIMARY)
        self.report_callback_exception = self._on_tk_error

        # Windows 任务栏图标：AppUserModelID 解除与 python.exe 的关联
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("xi.painting.board.v4")
            except Exception:
                pass
        # 设置图标：iconbitmap(.ico) 影响任务栏 + 窗口，iconphoto 补充 alt-tab 缩略图
        if os.path.exists(ICON_PATH) and ICON_PATH.endswith('.ico'):
            try:
                self.iconbitmap(default=ICON_PATH)
            except Exception:
                pass
            try:
                i = Image.open(ICON_PATH)
                self._icon_img = ImageTk.PhotoImage(i)
                self.iconphoto(True, self._icon_img)
            except Exception:
                pass

        gf = FONT_BODY
        style = ttk.Style()
        apply_theme(style)

        self._path = ""
        self._pix = None; self._img = None
        self._history = []; self._max_history = 50
        self._selected_color = None; self._manual_bg = None
        self._picking_bg = False; self._mode = MODE_PHOTO
        self._contour_enabled = True
        self._show_grid = True
        self._wait_hint_shown = False  # 运行耗时提示仅首次展示

        # ── 状态变量 ──
        self.bv = tk.IntVar(value=0)
        self.cv = tk.IntVar(value=0)
        self.sv = tk.IntVar(value=0)
        self.smoot_var = tk.IntVar(value=SMOOTH_STRENGTH)
        self.grid_n = tk.IntVar(value=DEFAULT_N)

        for v in (self.bv, self.cv, self.sv, self.smoot_var, self.grid_n):
            v.trace_add("write", lambda *_: self._safe_reprocess())

        self.zoom_var = tk.IntVar(value=100)
        self.zoom_display = tk.StringVar(value="100%")
        self._display_cell = CELL

        def _uz(*_):
            self.zoom_display.set(str(self.zoom_var.get()) + "%")
            self._display_image()
        self.zoom_var.trace_add("write", _uz)
        self.bind_all("<Control-z>", self._undo)

        # ── 顶栏 ──
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=12, pady=(12, 2))
        RoundedButton(top, text="打开图片", command=self.open_image).pack(side=tk.LEFT)
        self.file_lbl = ttk.Label(top, text="未选择图片", foreground=TEXT_SECONDARY)
        self.file_lbl.pack(side=tk.LEFT, padx=10)
        RoundedButton(top, text="适合窗口", command=self._fit_to_window).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Label(top, textvariable=self.zoom_display, width=5, anchor=tk.E).pack(side=tk.RIGHT, padx=(0, 2))
        ModernSlider(top, from_=25, to=200, orient=tk.HORIZONTAL, variable=self.zoom_var,
                     length=100).pack(side=tk.RIGHT)
        ttk.Label(top, text="缩放:").pack(side=tk.RIGHT, padx=(0, 2))

        # ── 控制面板（导航栏样式 ──
        ctrl = ttk.Frame(self)
        ctrl.pack(fill=tk.X, padx=12, pady=(2, 4))

        def _mk_nav_sl(parent, lab, var, v_min=-100, v_max=100, length=100):
            """导航栏span样式的滑块项"""
            f = ttk.Frame(parent)
            f.pack(side=tk.LEFT, padx=(0, 18))
            ttk.Label(f, text=lab).pack(side=tk.LEFT, padx=(0, 4))
            s = ModernSlider(f, from_=v_min, to=v_max, orient=tk.HORIZONTAL, variable=var,
                             length=length)
            s.pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(f, textvariable=var, width=4).pack(side=tk.LEFT)
            return s

        # ── Row 1: 图像调节 ──
        r1 = ttk.Frame(ctrl); r1.pack(fill=tk.X, pady=1)
        _mk_nav_sl(r1, "亮度", self.bv)
        _mk_nav_sl(r1, "对比度", self.cv)
        _mk_nav_sl(r1, "饱和度", self.sv)

        # ── Row 2: 图纸尺寸（滑块+输入框） | 边缘柔和度 ──
        r2 = ttk.Frame(ctrl); r2.pack(fill=tk.X, pady=1)

        gf = ttk.Frame(r2); gf.pack(side=tk.LEFT, padx=(0, 18))
        ttk.Label(gf, text="图纸尺寸:").pack(side=tk.LEFT, padx=(0, 4))
        self.grid_slider = ModernSlider(gf, from_=N_MIN, to=N_MAX, resolution=4,
                                     orient=tk.HORIZONTAL, variable=self.grid_n,
                                     length=100)
        self.grid_slider.pack(side=tk.LEFT, padx=(0, 4))
        self.grid_spin = tk.Spinbox(gf, from_=N_MIN, to=N_MAX, increment=4,
                                     textvariable=self.grid_n, width=5,
                                     bg=BG_PRIMARY, fg=TEXT_PRIMARY,
                                     buttonbackground=BG_TERTIARY,
                                     justify=tk.CENTER, bd=1,
                                     highlightbackground=BG_ACTIVE,
                                     font=FONT_BODY)
        self.grid_spin.pack(side=tk.LEFT, padx=(4, 0))

        sf = ttk.Frame(r2); sf.pack(side=tk.LEFT)
        _mk_nav_sl(sf, "边缘柔和度", self.smoot_var, 0, 9, length=100)

        # ── Row 3: 功能按钮 ──
        r3 = ttk.Frame(ctrl); r3.pack(fill=tk.X, pady=1)
        self.contour_btn = RoundedButton(r3,
            text="启用描边" if self._contour_enabled else "关闭描边",
            command=self._toggle_contour)
        self.contour_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.pick_bg_btn = RoundedButton(r3, text="拾取背景色", command=self._start_pick_bg)
        self.pick_bg_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.bg_status_lbl = ttk.Label(r3, text="自动检测", foreground=TEXT_SECONDARY)
        self.bg_status_lbl.pack(side=tk.LEFT, padx=(0, 10))

        self.grid_btn = RoundedButton(r3, text="隐藏网格", command=self._toggle_grid)
        self.grid_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.mode_var = tk.StringVar(value=MODE_NAMES[MODE_PHOTO])
        self.mode_combo = ttk.Combobox(r3, textvariable=self.mode_var,
            values=MODE_NAMES, state="readonly", width=17)
        self.mode_combo.pack(side=tk.LEFT, padx=(0, 6))
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)

        # ── 画布区 ──
        center = ttk.Frame(self)
        center.pack(fill=tk.BOTH, expand=True, padx=12, pady=2)
        self.canvas = tk.Canvas(center, bg=BG_PRIMARY, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas.bind("<Button-1>", self._on_grid_click)
        self.canvas.bind("<B1-Motion>", self._on_ctrl_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

        # ── 底栏 ──
        bot = ttk.Frame(self)
        bot.pack(fill=tk.X, padx=12, pady=(2, 8))
        self.info_lbl = ttk.Label(bot, text="就绪", foreground=TEXT_SECONDARY)
        self.info_lbl.pack(side=tk.LEFT)
        self.color_indicator = tk.Frame(bot, width=22, height=22, bg=BG_TERTIARY,
                                         highlightbackground=BG_ACTIVE, highlightthickness=1)
        self.color_indicator.pack(side=tk.LEFT, padx=(10, 4))
        self.sel_lbl = ttk.Label(bot, text="左键单点 | Ctrl拖动连画 | 右键取色 | Ctrl+Z撤销",
                                 foreground=TEXT_SECONDARY)
        self.sel_lbl.pack(side=tk.LEFT)
        RoundedButton(bot, text="自动绘图", command=self._open_auto_draw, accent=True).pack(side=tk.RIGHT, padx=(4, 0))
        RoundedButton(bot, text="颜色表", command=self._open_color_picker).pack(side=tk.RIGHT, padx=(4, 0))
        RoundedButton(bot, text="使用说明", command=self._show_start_tip).pack(side=tk.RIGHT)
        RoundedButton(bot, text="保存图纸", command=self.save_image).pack(side=tk.RIGHT)
        RoundedButton(bot, text="作者抖音", command=lambda: webbrowser.open(DOUYIN_URL)).pack(side=tk.RIGHT, padx=(4, 0))

        self._show_start_tip()

    # ═══════════════ 异常处理 ═══════════════
    @staticmethod
    def _on_tk_error(*args):
        err = traceback.format_exc() if args[0] is None else "".join(str(a) for a in args)
        log = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_crash.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n=== Tk callback crash ===\n{err}\n")
        try:
            messagebox.showerror("内部错误", f"发生未捕获异常，已写入 _crash.log\n\n{err[:2000]}")
        except Exception:
            pass

    def _safe_reprocess(self):
        try:
            self._maybe_reprocess()
        except Exception:
            self._on_tk_error(None, None, traceback.format_exc())

    def _reprocess(self):
        if not self._path: return
        try:
            self._pix = self._make_pattern(self._path)[1]
            an = self._actual_n()
            self._img = render_pattern(self._pix, CELL, n=an, show_grid=self._show_grid)
            self._calc_colors(); self._display_image(); self._fit_to_window()
        except Exception as e:
            self._on_tk_error(None, None, traceback.format_exc())
            messagebox.showerror("处理失败", f"无法处理图片：{e}")

    # ═══════════════ 模式切换 ═══════════════
    def _get_mode_name(self, mode):
        return MODE_NAMES[mode]

    def _on_mode_change(self, event=None):
        sel = self.mode_var.get()
        new_mode = MODE_NAMES.index(sel)
        if new_mode == self._mode:
            return
        # 切换模式时重置所有参数到默认值
        self.bv.set(0); self.cv.set(0); self.sv.set(0)
        self.smoot_var.set(SMOOTH_STRENGTH)
        self._manual_bg = None
        self.bg_status_lbl.config(text="自动检测", foreground=TEXT_SECONDARY)
        self._mode = new_mode
        self._safe_reprocess()

    # ═══════════════ 打开图片 ═══════════════
    def open_image(self):
        p = filedialog.askopenfilename(title="打开图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("所有文件", "*.*")])
        if not p: return
        try:
            Image.open(p)
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开图片文件：{e}"); return
        p = show_crop_dialog(self, p)
        if p is None: return
        self._path = p; self._reset_state()
        self.file_lbl.config(text=os.path.basename(p))
        self._safe_reprocess()

    def _reset_state(self):
        self._selected_color = None; self._manual_bg = None; self._picking_bg = False
        self._contour_enabled = True; self._mode = MODE_PHOTO
        self.mode_var.set(MODE_NAMES[MODE_PHOTO])
        self.contour_btn.config(text="启用描边"); self._history.clear()
        self.bg_status_lbl.config(text="自动检测", foreground=TEXT_SECONDARY)
        # 重置图像调节滑块到默认值
        self.bv.set(0); self.cv.set(0); self.sv.set(0)
        self.smoot_var.set(SMOOTH_STRENGTH)

    def _maybe_reprocess(self):
        if self._path: self._reprocess()

    def _actual_n(self):
        """返回当前 pix 的实际网格尺寸"""
        if self._pix is None:
            return self.grid_n.get()
        return int(len(self._pix) ** 0.5)

    def _make_pattern(self, path):
        # 算法运行前提示：部分算法运行可能太慢，请耐心等待（仅首次展示）
        if not self._wait_hint_shown:
            self._wait_hint_shown = True
            try:
                messagebox.showinfo("温馨提示", "部分算法运行可能太慢，请耐心等待", parent=self)
            except Exception:
                pass
        mc = MODE_CONFIG[self._mode]
        gn = self.grid_n.get()
        from PIL import Image, ImageEnhance

        # ── D 盘旧算法模式：主色法 / Q版 / 立绘 / 立绘进阶 ──
        algo = mc.get("algo")
        if algo:
            return self._make_pattern_legacy(path, gn, algo, mc)

        # ── 亮度 / 对比度 / 饱和度预处理 ──
        img = Image.open(path)
        bv, cv, sv = self.bv.get(), self.cv.get(), self.sv.get()
        if bv != 0 or cv != 0 or sv != 0:
            if bv != 0:
                img = ImageEnhance.Brightness(img).enhance(1.0 + bv / 100.0)
            if cv != 0:
                img = ImageEnhance.Contrast(img).enhance(1.0 + cv / 100.0)
            if sv != 0:
                img = ImageEnhance.Color(img).enhance(1.0 + sv / 100.0)
            import tempfile
            fd, temp_path = tempfile.mkstemp(suffix='.png', prefix='bead_gui_')
            os.close(fd)
            img.save(temp_path)
            work_path = temp_path
        else:
            work_path = path
            temp_path = None

        # ── 映射旧模式到新引擎参数 ──
        modes = list(mc["modes"])
        crop = mc["crop"]
        smooth = self.smoot_var.get()
        # catalog 固定为 40 色 PERLER_PALETTE；颜色数上限截断到 40，
        # 引擎只能在真实色板内选色，输出严格 ⊆ 40 色集合。
        max_colors = min(40, max(8, mc["max_colors"] - smooth))

        # 描边启用时优先使用 edge 模式
        if self._contour_enabled and "edge" not in modes:
            modes.insert(0, "edge")

        bg = self._manual_bg if self._manual_bg is not None else (255, 255, 255)

        # ── 调用新引擎 ──
        engine = BeadEngineOptimized(catalog=build_catalog())
        prepared, results = engine.generate(
            work_path, rows=gn, cols=gn,
            max_colors=max_colors, modes=modes,
            crop=crop, background=bg,
        )

        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass

        best = results[0]
        pix = [(int(best.grid[r, c, 0]), int(best.grid[r, c, 1]), int(best.grid[r, c, 2]))
               for r in range(gn) for c in range(gn)]

        return render_pattern(pix, n=gn, show_grid=self._show_grid), pix

    def _make_pattern_legacy(self, path, gn, algo, mc):
        """D 盘旧算法模式：主色法 / Q版预设 / 立绘预设 / 立绘进阶。
        所有链路末尾都量化到 40 色调色板（quantize_to_palette）。
        """
        from ..core.palette import quantize_to_palette
        bv, cv, sv = self.bv.get(), self.cv.get(), self.sv.get()
        max_colors = min(40, mc.get("max_colors", 40))

        if algo == "bead_render_precise":
            # bead_render 库改造版精准模式：加权全图 KMeans + 固定 40 色板，尺寸铁律 n 即 n
            from ..core.bead_render_engine import bead_render_precise
            pix, n = bead_render_precise(path, n=gn, palette_size=max_colors, crop="border")
        elif algo == "kmeans_beadify":
            # 完全 KMeans 精准模式：全图一次聚类，尺寸铁律 n 即 n
            from ..core.kmeans_beadify import kmeans_beadify
            pix, n = kmeans_beadify(path, n=gn, k=max(8, max_colors), crop="border")
        elif algo == "legacy_dominant":
            from ..core.bead_converter import image_to_beads
            small, n = image_to_beads(path, n=gn, palette=max_colors, crop="border",
                                      saturation=1.30, contrast=1.12, sharpen=0.6)
            pix = [tuple(int(v) for v in small[r, c]) for r in range(n) for c in range(n)]
        elif algo == "avatar":
            from ..core.presets import bead_avatar
            tile = bead_avatar(path, N=gn, max_colors=max_colors,
                               brightness=bv, contrast=cv, saturation=sv)
            pix = list(tile.getdata())
        elif algo == "portrait":
            from ..core.presets import bead_portrait
            tile = bead_portrait(path, N=gn, max_colors=max_colors,
                                 brightness=bv, contrast=cv, saturation=sv)
            pix = list(tile.getdata())
        elif algo == "portrait_pro":
            from ..core.presets import bead_portrait_pro
            tile = bead_portrait_pro(path, N=gn, max_colors=max_colors)
            pix = list(tile.getdata())
        else:
            raise ValueError(f"未知旧算法: {algo}")

        # 兜底：确保全部像素量化到 40 色调色板内（幂等）
        pix = quantize_to_palette(pix)
        return render_pattern(pix, n=gn, show_grid=self._show_grid), pix

    def _push_history(self):
        if self._pix is None: return
        if len(self._history) >= self._max_history:
            self._history.pop(0)
        self._history.append(self._pix.copy())

    def _undo(self, event=None):
        if not self._history: return
        self._pix = self._history.pop(); self._refresh()

    def _draw_pixel_at(self, sx, sy):
        if self._pix is None or self._selected_color is None: return False
        gn = self._actual_n()
        cx = self.canvas.canvasx(sx); cy = self.canvas.canvasy(sy)
        cell = self._display_cell; ps = gn * cell
        if not (ps <= cx < ps * 2 and 0 <= cy < ps): return False
        col = int((cx - ps) // cell); row = int(cy // cell)
        if not (0 <= row < gn and 0 <= col < gn): return False
        idx = row * gn + col
        if self._pix[idx] == self._selected_color: return False
        self._pix[idx] = self._selected_color; return True

    def _on_grid_click(self, event):
        if self._pix is None or self._selected_color is None: return
        self._push_history(); self._draw_pixel_at(event.x, event.y); self._refresh()

    def _on_ctrl_drag(self, event):
        if self._pix is None or self._selected_color is None: return
        if self._draw_pixel_at(event.x, event.y): self._refresh()

    def _on_release(self, event): pass

    def _on_right_click(self, event):
        if self._pix is None: return
        gn = self._actual_n()
        cx = self.canvas.canvasx(event.x); cy = self.canvas.canvasy(event.y)
        cell = self._display_cell
        if not (0 <= cx < gn * cell * 2 and 0 <= cy < gn * cell):
            self._picking_bg = False
            self.bg_status_lbl.config(text="自动检测", foreground=TEXT_SECONDARY); return
        col = int((cx % (gn * cell)) // cell); row = int(cy // cell)
        if not (0 <= row < gn and 0 <= col < gn): return
        picked = self._pix[row * gn + col]
        if self._picking_bg:
            self._manual_bg = picked; self._picking_bg = False
            self.bg_status_lbl.config(text=f"RGB({picked[0]},{picked[1]},{picked[2]})",
                                       foreground=SUCCESS)
            self._reprocess(); return
        self._selected_color = picked; self._update_color_indicator()

    def _start_pick_bg(self):
        if self._pix is None: return
        self._picking_bg = True
        self.bg_status_lbl.config(text="请右键点击图纸选取背景色", foreground=WARNING)

    def _display_image(self):
        if self._pix is None: return
        gn = self._actual_n()
        z = max(0.25, self.zoom_var.get() / 100.0)
        self._display_cell = max(10, int(CELL * z + 0.5))
        img = render_pattern(self._pix, self._display_cell, n=gn, show_grid=self._show_grid)
        self._tkimg = ImageTk.PhotoImage(img)
        self.canvas.config(scrollregion=(0, 0, img.width, img.height))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._tkimg)
        self._draw_axis(gn, self._display_cell)

    def _draw_axis(self, gn, cell):
        """绘制黑色中轴线，仅在右半编辑区将图案分为4个象限"""
        ps = gn * cell
        mid = ps // 2
        # 横向中轴线 — 仅右半编辑区
        self.canvas.create_line(ps, mid, ps * 2, mid,
                                fill="#000000", width=2, tags="axis")
        # 竖向中轴线 — 仅右半编辑区
        self.canvas.create_line(ps + mid, 0, ps + mid, ps,
                                fill="#000000", width=2, tags="axis")

    def _fit_to_window(self):
        if self._img is None: return
        self.update_idletasks()
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10 or ch < 10: return
        self.zoom_var.set(int(min(cw / self._img.width * 100, ch / self._img.height * 100)))

    def _on_ctrl_mousewheel(self, event):
        self.zoom_var.set(max(25, min(200, self.zoom_var.get() + int(event.delta / 120) * 5)))

    def _on_mousewheel(self, event):
        if event.state & 1:
            self.canvas.xview_scroll(int(-event.delta / 120), "units")
        else:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _calc_colors(self):
        an = self._actual_n()
        total = an * an
        cc = Counter(self._pix)
        self._sorted_colors = sorted(cc.items(), key=lambda x: (x[0][0], x[0][1], x[0][2]))
        nc = len(self._sorted_colors)
        cs = "描边开" if self._contour_enabled else "描边关"
        self.info_lbl.config(
            text=f"{an}×{an}={total}粒 | {nc}种颜色 | {cs} | {self._get_mode_name(self._mode)} | 柔和度:{self.smoot_var.get()}")

    def _update_color_indicator(self):
        if self._selected_color:
            r, g, b = self._selected_color
            self.color_indicator.config(bg=f"#{r:02x}{g:02x}{b:02x}")
            self.sel_lbl.config(text=f"RGB({r},{g},{b}) | 左键单点 | Ctrl连画 | 右键取色 | Ctrl+Z撤销")
        else:
            self.color_indicator.config(bg=BG_TERTIARY)
            self.sel_lbl.config(text="左键单点 | Ctrl拖动连画 | 右键取色 | Ctrl+Z撤销")

    def _refresh(self):
        gn = self._actual_n()
        self._img = render_pattern(self._pix, CELL, n=gn)
        self._calc_colors(); self._update_color_indicator(); self._display_image()

    def _set_toplevel_icon(self, win):
        """为子窗口设置图标（替代 monkey-patch Toplevel）"""
        if os.path.exists(ICON_PATH) and ICON_PATH.endswith('.ico'):
            try:
                win.iconbitmap(default=ICON_PATH)
            except Exception:
                pass

    def _open_auto_draw(self):
        """打开自动绘图对话框"""
        if self._pix is None:
            messagebox.showwarning("提示", "请先打开图片并生成图纸")
            return
        from ..auto_draw.dialog import AutoDrawDialog
        AutoDrawDialog(self, self._pix, self._actual_n(),
                       icon_setter=self._set_toplevel_icon)

    def _toggle_grid(self):
        self._show_grid = not self._show_grid
        self.grid_btn.config(text="显示网格" if not self._show_grid else "隐藏网格")
        self._refresh()

    def _toggle_contour(self):
        self._contour_enabled = not self._contour_enabled
        self.contour_btn.config(text="启用描边" if self._contour_enabled else "关闭描边")
        self._safe_reprocess()

    def _open_color_picker(self):
        if not self._sorted_colors: return
        win = tk.Toplevel(self); win.title("选择颜色"); win.configure(bg=BG_PRIMARY)
        win.geometry("680x480"); win.transient(self); win.grab_set()
        self._set_toplevel_icon(win)
        cv = tk.Canvas(win, bg=BG_PRIMARY, highlightthickness=0)
        sb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        frame = ttk.Frame(cv); cv.create_window((0, 0), window=frame, anchor=tk.NW)
        for i, (color, count) in enumerate(self._sorted_colors):
            r, c = divmod(i, 10)
            rv, gv, bv = color; hx = f"#{rv:02x}{gv:02x}{bv:02x}"
            cell = tk.Frame(frame, width=60, height=46, bg=BG_TERTIARY,
                           highlightbackground=BG_ACTIVE, highlightthickness=1)
            cell.grid(row=r, column=c, padx=2, pady=2); cell.pack_propagate(False)
            tk.Frame(cell, width=36, height=22, bg=hx).pack(pady=(2, 0))
            tk.Label(cell, text=str(count), bg=BG_TERTIARY, fg=TEXT_SECONDARY,
                    font=("Microsoft YaHei UI", 8)).pack()

            def _pick(ev, col=color, w=win):
                self._selected_color = col; self._update_color_indicator(); w.destroy()
            cell.bind("<Button-1>", _pick)
            for ch in cell.winfo_children(): ch.bind("<Button-1>", _pick)
        frame.update_idletasks()
        cv.configure(scrollregion=cv.bbox("all"))
        cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); sb.pack(side=tk.RIGHT, fill=tk.Y)

    def save_image(self):
        if self._img is None: return
        p = filedialog.asksaveasfilename(title="保存图纸", defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")])
        if p:
            try:
                self._img.save(p)
                # 附赠 n×n 像素 raw PNG（每格一像素、不放大、无网格线）
                raw_path = None
                if self._pix is not None:
                    from ..core.bead_render_engine import save_raw_png
                    gn = self._actual_n()
                    raw_path = os.path.splitext(p)[0] + f"_raw_{gn}x{gn}.png"
                    save_raw_png(self._pix, gn, raw_path)
                self.info_lbl.config(
                    text="已保存: " + os.path.basename(p) +
                         (f" + {os.path.basename(raw_path)}" if raw_path else ""))
            except Exception as e:
                messagebox.showerror("保存失败", str(e))

    def _show_start_tip(self):
        messagebox.showinfo("使用说明", START_TIP, parent=self)

    def run(self):
        self.mainloop()
