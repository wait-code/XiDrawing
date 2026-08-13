"""全局配置"""
import os, sys

# 网格尺寸范围
N_MIN = 12
DEFAULT_N = 24
N_MAX = 256

CELL = 40
SMOOTH_STRENGTH = 5

# 完整版图纸渲染边距（来自 D 盘原版 config）
MARGIN_TOP = 20
MARGIN_LEFT = 28

# 模式常量
# MODE_PHOTO 默认走 bead_render 库改造版（palette_optimize：边缘+中心加权全图 KMeans，
# 输出统一量化到固定 40 色板，尺寸铁律）。kmeans_beadify 保留为备选实现。
# 插画 / 边缘 / 抖动保留 BeadEngineOptimized 作为进阶模式
MODE_PHOTO = 0
MODE_ILLUSTRATION = 1
MODE_EDGE = 2
MODE_DITHER = 3
# 追加：D 盘旧算法模式（全部约束在 40 色调色板内）
MODE_LEGACY_DOMINANT = 4      # 主色法转换
MODE_LEGACY_AVATAR = 5        # Q版预设
MODE_LEGACY_PORTRAIT = 6      # 立绘预设
MODE_LEGACY_PORTRAIT_PRO = 7  # 立绘进阶
MODE_NAMES = ["精准模式 (KMeans)", "插画模式 (illustration)", "边缘模式 (edge)", "抖动模式 (dither)",
              "主色法 (dominant)", "Q版预设 (avatar)", "立绘预设 (portrait)", "立绘进阶 (portrait pro)"]

# 模式 → 算法/参数映射
MODE_CONFIG = {
    MODE_PHOTO:         {"algo": "bead_render_precise", "palette_size": 40, "crop": "border"},
    MODE_ILLUSTRATION:  {"modes": ["illustration"],      "max_colors": 32, "crop": "auto"},
    MODE_EDGE:          {"modes": ["edge"],              "max_colors": 32, "crop": "auto"},
    MODE_DITHER:        {"modes": ["dither"],            "max_colors": 32, "crop": "auto"},
    # 旧算法模式：走 core 下 legacy 算法链路，max_colors 不得超过 40
    MODE_LEGACY_DOMINANT:    {"algo": "legacy_dominant",   "max_colors": 40, "crop": "auto"},
    MODE_LEGACY_AVATAR:      {"algo": "avatar",            "max_colors": 40, "crop": "auto"},
    MODE_LEGACY_PORTRAIT:    {"algo": "portrait",          "max_colors": 40, "crop": "auto"},
    MODE_LEGACY_PORTRAIT_PRO: {"algo": "portrait_pro",     "max_colors": 40, "crop": "auto"},
}

# 性能优化开关：固定色板批量最近邻映射（cKDTree / numpy 兜底）
DEFAULT_USE_FAST_PALETTE = True

DITHER_NONE = 'none'
DITHER_FS = 'fs'
DITHER_ATKINSON = 'atkinson'
DITHER_JJN = 'jjn'
DITHER_STUCKI = 'stucki'
DITHER_NAMES = ["无", "Floyd-Steinberg", "Atkinson", "Jarvis-Judice-Ninke", "Stucki"]

DITHER_PALETTE_LEVELS = 8
DITHER_ERROR_THRESHOLD = 3.0

AUTHOR_NAME = ""
DOUYIN_URL = "https://v.douyin.com/"

# 图标路径：支持开发模式与 PyInstaller 打包模式
if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    _icon_candidate = os.path.join(_base, "icon.ico")
else:
    _proj_dir = os.path.dirname(os.path.abspath(__file__))
    _parent_dir = os.path.dirname(_proj_dir)
    _icon_candidate = os.path.join(_parent_dir, "icon.ico")
if os.path.exists(_icon_candidate):
    ICON_PATH = _icon_candidate
else:
    ICON_PATH = os.path.join(os.path.expanduser("~"), "Desktop", "tile.jpeg")
