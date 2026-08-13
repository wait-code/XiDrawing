"""屏幕截图：全屏 / 区域截图，返回 numpy ndarray"""
import numpy as np
from PIL import ImageGrab

# DPI 感知：让截图拿到真实物理像素，跨缩放比例(125%/150%)的显示器才能稳定匹配坐标与点击。
# 进程级设置，必须在任何截图/取坐标之前执行一次。
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    pass


class ScreenCapture:
    """屏幕截图封装，基于 PIL.ImageGrab"""

    def grab_full(self):
        """截取全屏，返回 BGR ndarray（OpenCV 格式）"""
        img = ImageGrab.grab()
        arr = np.array(img)  # RGB
        return arr[:, :, ::-1].copy()  # → BGR

    def grab_region(self, bbox):
        """截取指定区域 bbox=(x0, y0, x1, y1)，返回 BGR ndarray"""
        img = ImageGrab.grab(bbox=bbox)
        arr = np.array(img)
        return arr[:, :, ::-1].copy()

    def grab_gray(self, bbox=None):
        """截取灰度图，用于模板匹配"""
        if bbox:
            bgr = self.grab_region(bbox)
        else:
            bgr = self.grab_full()
        import cv2
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
