"""自动绘图对话框：提示 → 配置 → 检测 → 绘制 → 完成"""
import ctypes
import os
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import cv2
import numpy as np

from ..gui.theme import (apply_theme, BG_PRIMARY, BG_SECONDARY, BG_TERTIARY,
                          TEXT_PRIMARY, TEXT_SECONDARY, ACCENT, FONT_BODY,
                          FONT_MONO, WARNING, SUCCESS, DANGER, RADIUS_SM,
                          RADIUS_MD, RADIUS_LG, FONT_TITLE, FONT_SMALL)
from ..gui.widgets import RoundedButton, CardFrame, ModernSlider, GradientFrame
from .capture import ScreenCapture
from .detector import CanvasDetector, find_game_window, check_environment
from .color_mapper import DyePalette, build_color_mapping
from .controller import HumanMouse
from .auto_drawer import AutoDrawer, AutoDrawError


def _drag_scroll_ctypes(anchor_x, anchor_y, dist_px, down=True):
    """用 ctypes 模拟左键按住拖拽滚动（游戏不响应滚轮）

    down=True: 向上拖，把下方内容拉上来（露出 page 2）
    down=False: 向下拖，滚回顶部
    """
    time.sleep(0.1)
    ctypes.windll.user32.SetCursorPos(int(anchor_x), int(anchor_y))
    time.sleep(0.1)
    # 左键按下
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.1)

    sign = -1 if down else 1
    target_y = anchor_y + sign * dist_px
    steps = max(15, int(abs(dist_px) / 5))
    for i in range(1, steps + 1):
        t = i / steps
        et = 1 - (1 - t) ** 2  # ease-out
        cur_y = int(anchor_y + sign * dist_px * et)
        ctypes.windll.user32.SetCursorPos(int(anchor_x), cur_y)
        time.sleep(0.02)

    ctypes.windll.user32.SetCursorPos(int(anchor_x), int(target_y))
    time.sleep(0.1)
    # 左键释放
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


class AutoDrawDialog(tk.Toplevel):
    """自动绘图对话框 — 独立置顶浮窗"""

    def __init__(self, parent, pattern_pixels, grid_n, icon_setter=None):
        super().__init__(parent)
        self.title("自动绘图")
        self.configure(bg=BG_PRIMARY)

        self.pattern_pixels = pattern_pixels
        self.grid_n = grid_n
        self.icon_setter = icon_setter
        self._drawer = None
        self._thread = None
        self._closing = False  # 对话框正在关闭（防止后台线程往已销毁控件写数据）

        self.resizable(False, True)
        self.minsize(480, 800)
        self.attributes("-topmost", True)

        self._build_ui()

        self.update_idletasks()
        x, y = self._position_panel(parent)
        self.geometry(f"480x1260+{x}+{y}")

        if icon_setter:
            icon_setter(self)

    def _position_panel(self, parent):
        """计算浮窗位置：停靠主窗口右侧+8px，超出屏幕则贴边"""
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        win_w = 480
        win_h = 1260

        x = px + pw + 8
        y = py

        if x + win_w > screen_w:
            x = screen_w - win_w
        if y + win_h > screen_h:
            y = screen_h - win_h
        if y < 0:
            y = 0

        return x, y

    def _build_ui(self):
        # ── 渐变标题栏 ──
        header = GradientFrame(self, width=480, height=48,
                                color1=ACCENT, color2=BG_PRIMARY)
        header.pack(fill=tk.X, padx=0, pady=0)
        header.create_text(240, 24, text="自动绘图", fill=TEXT_PRIMARY,
                           font=FONT_TITLE, anchor=tk.CENTER)

        # ── 提示区（可折叠） ──
        self.tip_collapsed = tk.BooleanVar(value=False)
        tip_frame = CardFrame(self, text="使用前准备", padding=12)
        tip_frame.pack(fill=tk.X, padx=10, pady=(10, 4))

        tip_header = tk.Frame(tip_frame, bg=BG_SECONDARY)
        tip_header.pack(fill=tk.X)
        self.tip_toggle_btn = tk.Label(tip_header, text="▼ 收起提示",
                                        fg=TEXT_SECONDARY, bg=BG_SECONDARY,
                                        font=FONT_SMALL, cursor="hand2")
        self.tip_toggle_btn.pack(side=tk.RIGHT)
        self.tip_toggle_btn.bind("<Button-1>", self._toggle_tip)

        self.tip_content = tk.Frame(tip_frame, bg=BG_SECONDARY)
        self.tip_content.pack(fill=tk.X, pady=(4, 0))
        tip_text = (
            "1. 打开明日方舟，进入绘图活动界面\n"
            "2. 任意分辨率与系统缩放均自动适配（先 DPI 检测再执行），画布与染料板需同时可见\n"
            "3. 首次使用先「测试点击染料」验证光标可达\n"
            "4. 绘图过程中点击鼠标会自动暂停\n"
            "5. 暂停期间请勿移动鼠标或切换窗口\n"
            "6. 按 Esc 紧急停止\n"
            "7. 移动速度建议用 very_slow，太快易出现定位偏差\n"
            "8. 自动绘制完成后可能有少量格子漏填或错位，请手动检查修正"
        )
        tk.Label(self.tip_content, text=tip_text, justify=tk.LEFT,
                 fg=TEXT_SECONDARY, bg=BG_SECONDARY, font=FONT_BODY).pack(anchor=tk.W)

        # ── 配置区（竖向） ──
        cfg_frame = CardFrame(self, text="参数配置", padding=12)
        cfg_frame.pack(fill=tk.X, padx=10, pady=4)

        # 移动速度
        row1 = tk.Frame(cfg_frame, bg=BG_SECONDARY)
        row1.pack(fill=tk.X, pady=2)
        tk.Label(row1, text="移动速度", fg=TEXT_SECONDARY, bg=BG_SECONDARY,
                 font=FONT_BODY, width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.speed_var = tk.StringVar(value="very_slow")
        speed_combo = ttk.Combobox(row1, textvariable=self.speed_var,
                                    values=["very_slow", "slow", "medium", "fast"],
                                    state="readonly", width=14)
        speed_combo.pack(side=tk.LEFT, padx=(8, 0))

        # 抖动幅度
        row2 = tk.Frame(cfg_frame, bg=BG_SECONDARY)
        row2.pack(fill=tk.X, pady=2)
        tk.Label(row2, text="抖动幅度(px)", fg=TEXT_SECONDARY, bg=BG_SECONDARY,
                 font=FONT_BODY, width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.jitter_var = tk.IntVar(value=10)
        ttk.Spinbox(row2, from_=0, to=30, textvariable=self.jitter_var,
                     width=6).pack(side=tk.LEFT, padx=(8, 0))

        # 点击延迟
        row3 = tk.Frame(cfg_frame, bg=BG_SECONDARY)
        row3.pack(fill=tk.X, pady=2)
        tk.Label(row3, text="点击延迟(ms)", fg=TEXT_SECONDARY, bg=BG_SECONDARY,
                 font=FONT_BODY, width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.delay_var = tk.IntVar(value=250)
        ttk.Spinbox(row3, from_=100, to=1000, textvariable=self.delay_var,
                     width=6).pack(side=tk.LEFT, padx=(8, 0))

        # 颜色阈值
        row4 = tk.Frame(cfg_frame, bg=BG_SECONDARY)
        row4.pack(fill=tk.X, pady=2)
        tk.Label(row4, text="颜色阈值", fg=TEXT_SECONDARY, bg=BG_SECONDARY,
                 font=FONT_BODY, width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.threshold_var = tk.IntVar(value=10)
        ttk.Spinbox(row4, from_=0, to=30, textvariable=self.threshold_var,
                     width=6).pack(side=tk.LEFT, padx=(8, 0))

        # 点击方案
        row5 = tk.Frame(cfg_frame, bg=BG_SECONDARY)
        row5.pack(fill=tk.X, pady=2)
        tk.Label(row5, text="点击方案", fg=TEXT_SECONDARY, bg=BG_SECONDARY,
                 font=FONT_BODY, width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.click_method_var = tk.StringVar(value="sendinput")
        click_combo = ttk.Combobox(row5, textvariable=self.click_method_var,
                                    values=["sendinput", "mouse_event",
                                            "pydirectinput", "postmessage"],
                                    state="readonly", width=14)
        click_combo.pack(side=tk.LEFT, padx=(8, 0))

        # 调试模式
        row6 = tk.Frame(cfg_frame, bg=BG_SECONDARY)
        row6.pack(fill=tk.X, pady=2)
        tk.Label(row6, text="调试模式", fg=TEXT_SECONDARY, bg=BG_SECONDARY,
                 font=FONT_BODY, width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.debug_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row6, text="开启", variable=self.debug_var).pack(side=tk.LEFT, padx=(8, 0))

        # ── 状态区 ──
        status_frame = CardFrame(self, text="运行状态", padding=12)
        status_frame.pack(fill=tk.X, padx=10, pady=4)

        self.status_lbl = tk.Label(status_frame, text="就绪 — 点击「开始检测」",
                                    fg=TEXT_SECONDARY, bg=BG_SECONDARY,
                                    font=FONT_BODY, anchor=tk.W)
        self.status_lbl.pack(fill=tk.X)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(status_frame,
                                             variable=self.progress_var,
                                             maximum=100, length=380)
        self.progress_bar.pack(fill=tk.X, pady=(6, 2))

        self.progress_lbl = tk.Label(status_frame, text="0 / 0", fg=TEXT_SECONDARY,
                                      bg=BG_SECONDARY, font=FONT_BODY)
        self.progress_lbl.pack(anchor=tk.W)

        # ── 按钮区（两行横排） ──
        btn_frame = tk.Frame(self, bg=BG_PRIMARY)
        btn_frame.pack(fill=tk.X, padx=10, pady=(4, 4))

        row_btn1 = tk.Frame(btn_frame, bg=BG_PRIMARY)
        row_btn1.pack(fill=tk.X, pady=(0, 4))

        self.detect_btn = RoundedButton(row_btn1, text=" 1. 开始检测 ",
                                         command=self._on_detect,
                                         bg_color=BG_TERTIARY, text_color=TEXT_PRIMARY)
        self.detect_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.start_btn = RoundedButton(row_btn1, text=" 2. 开始绘制 ",
                                        command=self._on_start,
                                        bg_color=BG_TERTIARY, text_color=TEXT_PRIMARY)
        self.start_btn.config(state=tk.DISABLED)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.test_btn = RoundedButton(row_btn1, text=" 测试点击染料 ",
                                       command=self._on_test_click,
                                       bg_color=BG_TERTIARY, text_color=TEXT_PRIMARY)
        self.test_btn.config(state=tk.DISABLED)
        self.test_btn.pack(side=tk.LEFT)

        row_btn2 = tk.Frame(btn_frame, bg=BG_PRIMARY)
        row_btn2.pack(fill=tk.X)

        self.pause_btn = RoundedButton(row_btn2, text=" 暂停 ",
                                        command=self._on_pause,
                                        bg_color=BG_TERTIARY, text_color=TEXT_PRIMARY)
        self.pause_btn.config(state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.stop_btn = RoundedButton(row_btn2, text=" 停止 ",
                                       command=self._on_stop,
                                       bg_color=BG_TERTIARY, text_color=TEXT_PRIMARY)
        self.stop_btn.config(state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 6))

        RoundedButton(row_btn2, text=" 关闭 ", command=self._on_close,
                       bg_color=DANGER, text_color=TEXT_PRIMARY).pack(side=tk.RIGHT)

        # Esc 紧急停止
        self.bind("<Escape>", lambda e: self._on_stop())

        # ── 日志区（大幅扩展） ──
        log_frame = CardFrame(self, text="日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        self.log_text = tk.Text(log_frame, bg=BG_SECONDARY, fg=TEXT_PRIMARY,
                                insertbackground=TEXT_PRIMARY, relief=tk.FLAT,
                                font=FONT_MONO, wrap=tk.WORD)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _toggle_tip(self, event=None):
        if self.tip_collapsed.get():
            self.tip_content.pack(fill=tk.X, pady=(4, 0))
            self.tip_toggle_btn.config(text="▼ 收起提示")
            self.tip_collapsed.set(False)
        else:
            self.tip_content.pack_forget()
            self.tip_toggle_btn.config(text="▶ 展开提示")
            self.tip_collapsed.set(True)

    def _log(self, msg):
        """线程安全的日志写入（对话框关闭后自动丢弃）"""
        if self._closing:
            return
        def _append():
            if self.winfo_exists():
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
        self.after(0, _append)

    def _set_status(self, text):
        if self._closing:
            return
        def _update():
            if self.winfo_exists():
                self.status_lbl.config(text=text)
        self.after(0, _update)

    def _set_progress(self, done, total):
        if self._closing:
            return
        def _update():
            if self.winfo_exists():
                pct = done / total * 100 if total else 0
                self.progress_var.set(pct)
                self.progress_lbl.config(text=f"{done} / {total}  ({pct:.1f}%)")
        self.after(0, _update)

    def _safe_after(self, fn):
        """安全地调度 UI 回调：对话框关闭或已销毁时自动跳过。"""
        if self._closing:
            return
        def _guarded():
            if self.winfo_exists():
                try:
                    fn()
                except tk.TclError:
                    pass
        self.after(0, _guarded)

    def _generate_preview(self, result, mapping):
        """生成调试图，标注画布、染料板格子和将要点击的染料中心"""
        try:
            cap = ScreenCapture()
            screen = cap.grab_full()
            if screen is None or screen.size == 0:
                return

            # 检测结果为物理屏幕坐标；截图为 ImageGrab 图像坐标。
            # 多显示器（虚拟屏原点非 0）时必须先换算，否则标注错位。
            from .screen_info import physical_to_image
            def _img(x, y):
                return physical_to_image(x, y)

            preview = screen.copy()

            # 画布矩形与网格
            cx, cy, cw, ch = result.canvas_x, result.canvas_y, result.canvas_w, result.canvas_h
            cx, cy = _img(cx, cy)
            cv2.rectangle(preview, (cx, cy), (cx + cw, cy + ch), (0, 255, 0), 2)
            for i in range(1, result.grid_n):
                x = cx + i * result.cell_w
                cv2.line(preview, (x, cy), (x, cy + ch), (0, 255, 0), 1)
                y = cy + i * result.cell_h
                cv2.line(preview, (cx, y), (cx + cw, y), (0, 255, 0), 1)

            # 染料板矩形
            px, py = result.dye_panel_x, result.dye_panel_y
            px, py = _img(px, py)
            pw, ph = result.dye_panel_w, result.dye_panel_h
            cv2.rectangle(preview, (px, py), (px + pw, py + ph), (255, 0, 0), 2)

            # 估算 5 行可见区（整页）
            cell_h = ph / 5.0
            visible_h = int(cell_h * 5)
            cv2.rectangle(preview, (px, py), (px + pw, py + visible_h), (255, 255, 0), 2)

            # 标注会用到的染料格中心
            used_dyes = set()
            for dye in mapping.values():
                if dye is not None:
                    used_dyes.add(dye)

            cell_w = pw / 4.0
            for dye in used_dyes:
                grow = dye.row if dye.page == 1 else 5 + dye.row
                gcol = dye.col
                if grow < 5:
                    cx = int(px + gcol * cell_w + cell_w / 2)
                    cy = int(py + grow * cell_h + cell_h / 2)
                    cv2.circle(preview, (cx, cy), 5, (0, 0, 255), -1)
                    cv2.putText(preview, f"{dye.index}", (cx + 6, cy - 6),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

            # 保存
            debug_dir = os.path.join(os.path.dirname(__file__), "..", "..", "debug")
            os.makedirs(debug_dir, exist_ok=True)
            path = os.path.join(debug_dir, "auto_draw_preview.png")
            cv2.imwrite(path, preview)
            self._log(f"🖼 调试图已保存: {os.path.abspath(path)}")
        except Exception as e:
            self._log(f"⚠ 生成调试图失败: {e}")

    def _on_detect(self):
        """执行屏幕检测"""
        self.detect_btn.config(state=tk.DISABLED)
        self._set_status("正在截图检测...")
        self._log("🔍 开始屏幕检测...")

        def _detect_thread():
            try:
                cap = ScreenCapture()
                screen = cap.grab_full()

                self._log("正在匹配画布模板...")
                detector = CanvasDetector()
                # 主流做法：先锚定游戏窗口客户区，仅在窗口内检测，
                # 消除全屏假阳性并天然适配 DPI/分辨率；找不到窗口则回退全屏。
                client_rect = find_game_window()
                if client_rect:
                    self._log(f"   已锚定游戏窗口客户区: {client_rect[2]}×{client_rect[3]} @"
                              f"({client_rect[0]},{client_rect[1]})")
                else:
                    self._log("   ⚠ 未找到游戏窗口(标题含'明日方舟')，回退全屏检测")

                # 前置环境校验（DPI 自适应）：自动检测缩放/分辨率并核对窗口与模板
                env_ok, env_msgs = check_environment(client_rect)
                for m in env_msgs:
                    prefix = "   ✅ " if ("已锚定" in m or "DPI" in m or "缺失" not in m and "失败" not in m) else "   ⚠ "
                    self._log(prefix + m)
                if not env_ok:
                    self._log("   ⚠ 环境未完全就绪：请确保游戏窗口可见且标题含「明日方舟」")

                result = detector.detect(screen, client_rect)

                if not result.success:
                    self._log(f"❌ {result.message}")
                    self._safe_after(lambda: self.detect_btn.config(state=tk.NORMAL))
                    self._set_status("检测失败 — 请检查游戏界面")
                    return

                self._log(f"✅ 画布定位: ({result.canvas_x}, {result.canvas_y}) "
                          f"{result.canvas_w}×{result.canvas_h}")
                self._log(f"   网格: {result.grid_n}×{result.grid_n}, "
                          f"格大小: {result.cell_w}×{result.cell_h}")
                self._log(f"   染料板: ({result.dye_panel_x}, {result.dye_panel_y})")

                self._log("正在提取染料颜色...")
                palette = DyePalette()
                palette.load()
                self._log(f"   静态模板提取到 {len(palette.dyes)} 种染料")

                # 从屏幕实时取色：滚动染料板捕获 page 2
                if result.dye_panel_w > 0 and result.dye_panel_h > 0:
                    self._log("正在从屏幕实时取色...")
                    self._capture_dyes_from_screen(result, palette, cap)

                self._log(f"   最终染料池: {len(palette.dyes)} 种")

                self._log("正在建立颜色映射...")
                threshold = self.threshold_var.get()
                mapping = build_color_mapping(self.pattern_pixels, palette,
                                              threshold=threshold)
                self._log(f"   图纸 {len(mapping)} 种颜色 → {len(palette.dyes)} 种染料 "
                          f"(阈值 {threshold})")

                # 存储结果
                self._det_result = result
                self._palette = palette
                self._mapping = mapping

                # 验证网格数匹配
                if result.grid_n != self.grid_n:
                    self._log(f"⚠ 警告: 游戏画布 {result.grid_n}×{result.grid_n} "
                              f"与图纸 {self.grid_n}×{self.grid_n} 不一致")

                # 生成调试图（默认生成，便于核对坐标）
                self._generate_preview(result, mapping)

                self._set_status("检测完成 — 点击「测试点击染料」验证，或「开始绘制」")
                self._safe_after(lambda: self.start_btn.config(state=tk.NORMAL))
                self._safe_after(lambda: self.test_btn.config(state=tk.NORMAL))
                self._safe_after(lambda: self.detect_btn.config(state=tk.NORMAL))

            except Exception as e:
                self._log(f"❌ 检测异常: {e}")
                self._set_status("检测失败")
                self._safe_after(lambda: self.detect_btn.config(state=tk.NORMAL))

        threading.Thread(target=_detect_thread, daemon=True).start()

    def _on_start(self):
        """开始绘制"""
        if not hasattr(self, '_det_result'):
            messagebox.showwarning("提示", "请先执行检测", parent=self)
            return

        self.start_btn.config(state=tk.DISABLED)
        self.detect_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL)
        self._set_status("绘制中...")
        self._log("▶ 开始自动绘制...")

        def _draw_thread():
            try:
                debug = self.debug_var.get()
                mouse = HumanMouse(
                    speed=self.speed_var.get(),
                    jitter=self.jitter_var.get(),
                    click_delay=(self.delay_var.get(), self.delay_var.get() + 80),
                    debug=debug,
                    click_method=self.click_method_var.get(),
                )
                self._mouse = mouse

                drawer = AutoDrawer(
                    mouse=mouse,
                    detector_result=self._det_result,
                    palette=self._palette,
                    color_mapping=self._mapping,
                    on_progress=self._set_progress,
                    on_log=self._log,
                    on_status=self._set_status,
                    debug=debug,
                )
                self._drawer = drawer

                stats = drawer.draw(self.pattern_pixels)
                self._log(f"统计: 绘制 {stats['drawn']}, 跳过 {stats['skipped']}, "
                          f"总 {stats['total']}")

                self._safe_after(lambda: self._on_draw_finished())

            except AutoDrawError as e:
                # 运行防护触发：绘制失败/识别超时立即终止任务，弹窗告知用户
                self._log(f"⛔ 自动绘制已终止: {e}")
                self._safe_after(lambda: messagebox.showwarning(
                    "绘制已终止", f"{e}\n\n已停止自动绘制以保护画布，请检查页面状态后重试。",
                    parent=self))
                self._safe_after(lambda: self._on_draw_finished())

            except Exception as e:
                self._log(f"❌ 绘制异常: {e}")
                self._safe_after(lambda: self._on_draw_finished())

        self._thread = threading.Thread(target=_draw_thread, daemon=True)
        self._thread.start()

    def _on_test_click(self):
        """测试点击第一个染料格，验证鼠标能真正到达染料区并点击"""
        if not hasattr(self, '_det_result'):
            messagebox.showwarning("提示", "请先执行检测", parent=self)
            return

        self.test_btn.config(state=tk.DISABLED)
        self.start_btn.config(state=tk.DISABLED)
        self._set_status("测试点击中... 请观察光标是否移动到右侧染料区")
        self._log("🧪 测试点击：光标应缓慢移动到右侧染料板第一个颜色格并点击")

        def _test_thread():
            try:
                debug = self.debug_var.get()
                mouse = HumanMouse(
                    speed=self.speed_var.get(),
                    jitter=self.jitter_var.get(),
                    click_delay=(self.delay_var.get(), self.delay_var.get() + 80),
                    debug=debug,
                    click_method=self.click_method_var.get(),
                )
                self._mouse = mouse

                drawer = AutoDrawer(
                    mouse=mouse,
                    detector_result=self._det_result,
                    palette=self._palette,
                    color_mapping=self._mapping,
                    on_progress=self._set_progress,
                    on_log=self._log,
                    on_status=self._set_status,
                    debug=debug,
                )
                self._drawer = drawer

                drawer.test_click_dye(0)
                self._log("✅ 测试点击结束")
                self._set_status("测试完成 — 如光标未到达染料区，请检查分辨率和窗口遮挡")

            except Exception as e:
                self._log(f"❌ 测试点击异常: {e}")
                self._set_status("测试失败")
            finally:
                self._safe_after(lambda: self.test_btn.config(state=tk.NORMAL))
                self._safe_after(lambda: self.start_btn.config(state=tk.NORMAL))

        threading.Thread(target=_test_thread, daemon=True).start()

    def _capture_dyes_from_screen(self, result, palette, cap):
        """在检测阶段滚动染料板，从屏幕实时取色覆盖静态模板颜色。

        流程：
        1. 从屏幕当前染料板可见区域提取 page 1 染料（覆盖静态 colors1 结果）
        2. 鼠标滚轮下滑染料板，截图提取 page 2 染料
        3. 滚回顶部恢复
        """
        try:
            px, py = result.dye_panel_x, result.dye_panel_y
            pw, ph = result.dye_panel_w, result.dye_panel_h

            # cell 高度 = 模板高 / 5（每页 5 行）
            cell_h = ph / 5.0
            # 可见区高度 = cell_h × 5（整页 5 行）
            visible_h = int(cell_h * 5)

            # ── 提取 page 1（顶部可见区 5 行） ──
            page1_region = cap.grab_region((px, py, px + pw, py + int(cell_h * 5)))
            if page1_region is not None and page1_region.size > 0:
                # 清空静态模板的 page 1 结果，用屏幕颜色覆盖
                palette.dyes = [d for d in palette.dyes if d.page != 1]
                palette.extract_from_region(page1_region, page=1, rows=5)

            # ── 拖拽滚动到 page 2（左键按住向上拉） ──
            anchor_x = px + pw // 2
            anchor_y = py + visible_h // 2
            # 向上拖 5 行高度（整页）
            scroll_dist = int(cell_h * 5)
            _drag_scroll_ctypes(anchor_x, anchor_y, scroll_dist, down=True)
            time.sleep(0.4)

            # ── 提取 page 2（顶部可见区 5 行） ──
            page2_region = cap.grab_region((px, py, px + pw, py + int(cell_h * 5)))
            if page2_region is not None and page2_region.size > 0:
                # 清空静态模板的 page 2 结果，用屏幕颜色覆盖
                palette.dyes = [d for d in palette.dyes if d.page != 2]
                palette.extract_from_region(page2_region, page=2, rows=5)

            # ── 拖拽滚回顶部（左键按住向下拉） ──
            _drag_scroll_ctypes(anchor_x, anchor_y, scroll_dist, down=False)
            time.sleep(0.3)

        except Exception as e:
            self._log(f"  ⚠ 屏幕实时取色异常: {e}，将使用静态模板颜色")

    def _on_draw_finished(self):
        if not self.winfo_exists():
            return
        self.start_btn.config(state=tk.NORMAL)
        self.test_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)
        self._set_status("绘制结束")

    def _on_pause(self):
        if self._drawer:
            if self._drawer._paused:
                self._drawer.resume()
                self.pause_btn.config(text=" 暂停 ")
            else:
                self._drawer.pause()
                self.pause_btn.config(text=" 继续 ")

    def _on_stop(self):
        if self._drawer:
            self._drawer.stop()
        if not self.winfo_exists():
            return
        self.pause_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)

    def _on_close(self):
        """安全关闭：先标记关闭 → 停止绘制 → 等待后台线程 → 再销毁窗口。

        修复历史 TclError：旧实现 stop() 后立即 destroy()，
        后台线程仍通过 after(0, ...) 往已销毁控件写数据 → invalid command name。
        """
        # 1) 立即标记关闭，后续所有 UI 回调（_log/_set_status/_set_progress）会自动丢弃
        self._closing = True

        # 2) 如果正在绘制/检测，确认后停止
        if self._drawer and not self._drawer._stopped:
            if not messagebox.askokcancel("确认", "绘制进行中，确定关闭？", parent=self):
                self._closing = False  # 用户取消，恢复
                return
            self._drawer.stop()

        # 3) 等待后台线程结束（最多 2 秒，避免卡死）
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        # 4) 线程已退出或超时，安全销毁
        try:
            self.destroy()
        except tk.TclError:
            pass  # 窗口已被外部销毁（如主窗口退出），忽略
