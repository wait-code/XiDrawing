"""现代自定义 tkinter 组件

提供圆角按钮、卡片面板、精致滑块和渐变面板，
统一风格，兼容 ttk.Button 的 config(state=, text=) 接口。
"""
import math
import tkinter as tk
from tkinter import ttk, font as tkfont

from .theme import (BG_PRIMARY, BG_SECONDARY, BG_TERTIARY, BG_HOVER, BG_ACTIVE,
                     ACCENT, ACCENT_HOVER, TEXT_PRIMARY, TEXT_SECONDARY,
                     TEXT_TERTIARY, FONT_BODY, RADIUS_SM, RADIUS_MD)


def _round_rect_coords(x0, y0, x1, y1, r):
    """生成圆角矩形多边形坐标点列表"""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = []
    pts += [x0 + r, y0, x1 - r, y0]
    for a in range(-90, 0, 10):
        rad = math.radians(a)
        pts += [x1 - r + r * math.cos(rad), y0 + r + r * math.sin(rad)]
    pts += [x1, y0 + r, x1, y1 - r]
    for a in range(0, 91, 10):
        rad = math.radians(a)
        pts += [x1 - r + r * math.cos(rad), y1 - r + r * math.sin(rad)]
    pts += [x1 - r, y1, x0 + r, y1]
    for a in range(90, 181, 10):
        rad = math.radians(a)
        pts += [x0 + r + r * math.cos(rad), y1 - r + r * math.sin(rad)]
    pts += [x0, y1 - r, x0, y0 + r]
    for a in range(180, 271, 10):
        rad = math.radians(a)
        pts += [x0 + r + r * math.cos(rad), y0 + r + r * math.sin(rad)]
    return pts


class RoundedButton(tk.Canvas):
    """圆角按钮：Canvas 绘制圆角矩形 + 悬停变色 + 点击动画

    兼容 ttk.Button 的 config(state=..., text=...) 接口，
    可直接替换 ttk.Button 使用。
    """

    def __init__(self, parent, text="", command=None, width=None, height=30,
                 bg_color=BG_TERTIARY, hover_color=BG_HOVER,
                 text_color=TEXT_PRIMARY, font=FONT_BODY, accent=False,
                 parent_bg=BG_PRIMARY, **kwargs):
        self._font = font
        self._text = text
        self._command = command
        self._state = "normal"
        self._bg_color = ACCENT if accent else bg_color
        self._hover_color = ACCENT_HOVER if accent else hover_color
        self._text_color = "#FFFFFF" if accent else text_color
        self._parent_bg = parent_bg
        self._height = height

        if width is None:
            try:
                f = tkfont.Font(font=font)
                width = f.measure(text) + 28
            except Exception:
                width = 80

        self._width = width
        super().__init__(parent, width=width, height=height,
                         bg=parent_bg, highlightthickness=0, **kwargs)

        self._draw(self._bg_color)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self, color):
        self.delete("all")
        pts = _round_rect_coords(1, 1, self._width - 1, self._height - 1, RADIUS_SM)
        self.create_polygon(pts, fill=color, outline="")
        txt_color = self._text_color if self._state == "normal" else TEXT_TERTIARY
        self.create_text(self._width // 2, self._height // 2,
                         text=self._text, fill=txt_color, font=self._font)

    def _on_enter(self, _):
        if self._state == "normal":
            self._draw(self._hover_color)

    def _on_leave(self, _):
        if self._state == "normal":
            self._draw(self._bg_color)

    def _on_press(self, _):
        if self._state == "normal":
            self._draw(BG_ACTIVE)

    def _on_release(self, _):
        if self._state == "normal":
            self._draw(self._hover_color)
            if self._command:
                self._command()

    def config(self, **kwargs):
        changed = False
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            try:
                f = tkfont.Font(font=self._font)
                self._width = f.measure(self._text) + 28
                self.configure(width=self._width)
            except Exception:
                pass
            changed = True
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            changed = True
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if changed:
            color = self._bg_color if self._state == "normal" else BG_SECONDARY
            self._draw(color)
        if kwargs:
            super().config(**kwargs)

    configure = config


class CardFrame(ttk.LabelFrame):
    """卡片式面板：带圆角边框和可选标题

    继承自 ttk.LabelFrame，样式由 apply_theme() 统一配置。
    用法与 ttk.LabelFrame 完全一致。
    """
    pass


class ModernSlider(tk.Scale):
    """精致滑块：自定义配色，替代原生 tk.Scale

    颜色与主题统一，功能与 tk.Scale 完全一致。
    """

    def __init__(self, parent, **kwargs):
        defaults = dict(
            bg=BG_SECONDARY,
            fg=TEXT_PRIMARY,
            troughcolor=BG_TERTIARY,
            highlightthickness=0,
            bd=0,
            activebackground=ACCENT,
            sliderrelief="flat",
            font=FONT_BODY,
        )
        defaults.update(kwargs)
        super().__init__(parent, **defaults)


class GradientFrame(tk.Canvas):
    """渐变背景面板：逐行绘制垂直渐变

    用于标题栏等需要视觉层次的区域。
    """

    def __init__(self, parent, color1=BG_SECONDARY, color2=BG_PRIMARY,
                 height=40, **kwargs):
        super().__init__(parent, height=height, highlightthickness=0,
                         bg=BG_PRIMARY, **kwargs)
        self._color1 = color1
        self._color2 = color2
        self._h = height
        self.bind("<Configure>", self._draw_gradient)

    def _draw_gradient(self, event=None):
        self.delete("all")
        w = self.winfo_width()
        if w < 2:
            return
        r1, g1, b1 = (int(self._color1[1:3], 16),
                      int(self._color1[3:5], 16),
                      int(self._color1[5:7], 16))
        r2, g2, b2 = (int(self._color2[1:3], 16),
                      int(self._color2[3:5], 16),
                      int(self._color2[5:7], 16))
        for i in range(self._h):
            ratio = i / max(1, self._h - 1)
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
            color = f"#{r:02x}{g:02x}{b:02x}"
            self.create_line(0, i, w, i, fill=color)
