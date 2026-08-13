"""统一现代深色主题系统

为整个应用提供一致的配色体系、字体规范和 ttk 样式配置。
所有自定义组件和主窗口都引用此处的常量。
"""

# ═══════════════════ 色彩系统 ═══════════════════
# 背景层级（由深到浅）
BG_PRIMARY = "#0F0F1A"       # 主背景（最深，窗口底色）
BG_SECONDARY = "#1A1A2E"    # 次级背景（面板/输入框底色）
BG_TERTIARY = "#252540"     # 三级背景（按钮/卡片底色）
BG_HOVER = "#2F2F50"        # 悬停状态
BG_ACTIVE = "#3A3A5E"       # 激活/按下状态

# 强调色
ACCENT = "#6C7BFF"          # 主强调色（紫蓝，自动绘图等核心操作）
ACCENT_HOVER = "#8B98FF"    # 强调色悬停
ACCENT_DARK = "#4A56C7"     # 强调色暗

# 文字色
TEXT_PRIMARY = "#E0E0EC"    # 主文字
TEXT_SECONDARY = "#A0A0BC"  # 次要文字
TEXT_TERTIARY = "#6A6A85"   # 提示/禁用文字

# 功能色
SUCCESS = "#4ADE80"
WARNING = "#FBBF24"
DANGER = "#F87171"
INFO = "#60A5FA"

# 字体
FONT_BODY = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 8)
FONT_MONO = ("Consolas", 9)

# 圆角半径
RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14


def apply_theme(style):
    """统一配置 ttk.Style，在 App.__init__ 中调用一次即可

    Args:
        style: ttk.Style() 实例
    """
    style.theme_use("clam")
    style.configure(".", font=FONT_BODY)

    # ── Frame ──
    style.configure("TFrame", background=BG_PRIMARY)

    # ── Label ──
    style.configure("TLabel", background=BG_PRIMARY, foreground=TEXT_PRIMARY)
    style.configure("Secondary.TLabel", background=BG_PRIMARY, foreground=TEXT_SECONDARY)
    style.configure("Tertiary.TLabel", background=BG_PRIMARY, foreground=TEXT_TERTIARY)

    # ── Button ──
    style.configure("TButton", background=BG_TERTIARY, foreground=TEXT_PRIMARY,
                     borderwidth=0, focusthickness=0, font=FONT_BODY,
                     padding=(12, 6))
    style.map("TButton",
              background=[("active", BG_HOVER), ("disabled", BG_SECONDARY)],
              foreground=[("disabled", TEXT_TERTIARY)])

    # 强调色按钮（自动绘图等核心操作）
    style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                     borderwidth=0, focusthickness=0, font=FONT_BODY,
                     padding=(12, 6))
    style.map("Accent.TButton",
              background=[("active", ACCENT_HOVER), ("disabled", BG_TERTIARY)],
              foreground=[("disabled", TEXT_TERTIARY)])

    # ── Labelframe（卡片容器）──
    style.configure("TLabelframe", background=BG_SECONDARY, foreground=TEXT_PRIMARY,
                     borderwidth=1, relief="flat")
    style.configure("TLabelframe.Label", background=BG_SECONDARY,
                     foreground=TEXT_PRIMARY, font=FONT_BODY)

    # ── Combobox ──
    style.configure("TCombobox", fieldbackground=BG_TERTIARY, background=BG_TERTIARY,
                     foreground=TEXT_PRIMARY, borderwidth=0, padding=(4, 2),
                     arrowcolor=TEXT_SECONDARY)
    style.map("TCombobox",
              fieldbackground=[("readonly", BG_TERTIARY)],
              background=[("active", BG_HOVER)])

    # ── Scrollbar ──
    style.configure("TScrollbar", background=BG_TERTIARY, troughcolor=BG_SECONDARY,
                     borderwidth=0, arrowcolor=TEXT_SECONDARY)
    style.map("TScrollbar", background=[("active", BG_HOVER)])

    # ── Progressbar ──
    style.configure("TProgressbar", background=ACCENT, troughcolor=BG_TERTIARY,
                     borderwidth=0)

    # ── Checkbutton ──
    style.configure("TCheckbutton", background=BG_PRIMARY, foreground=TEXT_PRIMARY)
    style.map("TCheckbutton", background=[("active", BG_PRIMARY)])

    # ── Spinbox ──
    style.configure("TSpinbox", fieldbackground=BG_TERTIARY,
                     foreground=TEXT_PRIMARY, arrowcolor=TEXT_SECONDARY,
                     borderwidth=0)

    # ── Entry ──
    style.configure("TEntry", fieldbackground=BG_TERTIARY, foreground=TEXT_PRIMARY,
                     borderwidth=0, padding=(4, 2))

    # ── Separator ──
    style.configure("TSeparator", background=BG_TERTIARY)
