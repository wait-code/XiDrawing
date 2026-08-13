"""自动绘图模块：模板匹配定位 + 人类化键鼠 + 颜色映射"""
from .capture import ScreenCapture
from .detector import CanvasDetector, DetectionResult
from .color_mapper import DyePalette, build_color_mapping
from .controller import HumanMouse
from .auto_drawer import AutoDrawer

__all__ = [
    "ScreenCapture", "CanvasDetector", "DetectionResult",
    "DyePalette", "build_color_mapping",
    "HumanMouse", "AutoDrawer",
]
