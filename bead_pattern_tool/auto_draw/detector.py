"""画布定位（MAA 零模板方法）：比例坐标粗定位 + 网格线精校准

参考 arknights-pixel-autofill v1.3.0（JayJokerr / MAA 社区方案）：
- 不依赖任何模板图片；
- 先按 1280×720 基准比例坐标粗定位画布（_uncalibrated_scaled / _viewport）；
- 再用「网格线二阶差分响应 + 等间距拟合」做精校准（_detect_grid_axis），
  拿到 25 条真实网格线后取两线中点为格心，天然自适应分辨率/DPI/窗口尺寸；
- 检测失败自动回退比例坐标均分（_grid_center 兜底）。
"""
import os
import ctypes
from ctypes import wintypes
from statistics import median

import cv2
import numpy as np

from .screen_info import physical_to_image, image_to_physical, get_screen_info


def find_game_window(title_keyword="明日方舟"):
    """定位游戏窗口并返回客户区矩形（物理像素）(x, y, w, h, hwnd)。

    主流做法（参考 arknights-pixel-autofill / MAA）：不直接全屏撒网，
    而是先锚定游戏窗口客户区，后续所有检测/坐标都在该区域内进行，
    天然免疫全屏假阳性，且因本进程已设 DPI 感知(capture.py)，
    返回坐标与 ImageGrab 截图坐标系一致。

    找不到（标题不含关键字的窗口、非 Windows、或被其他进程占用）返回 None。
    """
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return None

    found = {"hwnd": None}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_keyword in buf.value:
            found["hwnd"] = hwnd
            return False  # 找到即停止枚举
        return True

    try:
        user32.EnumWindows(_enum_cb, 0)
    except Exception:
        return None

    hwnd = found["hwnd"]
    if not hwnd:
        return None

    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(rect.left, rect.top)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return (pt.x, pt.y, w, h, hwnd)


def check_environment(client_rect=None):
    """前置环境校验（零模板版，MAA 方法）。

    自动检测 DPI 缩放/物理与逻辑分辨率并打印；
    校验游戏窗口是否锚定。
    不再检查任何模板文件——画布定位使用「比例坐标 + 网格线校准」，
    染料颜色由 dialog 从屏幕实时取色，全程零模板依赖。

    Returns:
        (ok, messages) — ok 为可继续检测；messages 为日志/警告列表。
    """
    from .screen_info import get_dpi_profile
    msgs = []
    ok = True
    try:
        profile = get_dpi_profile()
        pw, ph = profile["physical"]
        lw, lh = profile["logical"]
        msgs.append(
            f"DPI 检测: 缩放 {profile['scale']:.0%}，"
            f"物理分辨率 {pw}×{ph}，逻辑分辨率 {lw}×{lh}，"
            f"显示器 {profile['monitor_count']} 台"
        )
    except Exception as e:  # pragma: no cover
        msgs.append(f"屏幕信息读取失败: {e}")

    if client_rect:
        cw, ch = client_rect[2], client_rect[3]
        msgs.append(
            f"已锚定游戏窗口客户区 {cw}×{ch}（物理像素），"
            "使用比例坐标+网格线校准定位（零模板）"
        )
    else:
        ok = False
        msgs.append("未锚定游戏窗口，回退全屏检测（建议游戏窗口标题含「明日方舟」后重试）")
    return ok, msgs


class DetectionResult:
    """检测结果容器"""

    def __init__(self):
        self.canvas_x = 0       # 画布左上角 x
        self.canvas_y = 0       # 画布左上角 y
        self.canvas_w = 0       # 画布宽度
        self.canvas_h = 0       # 画布高度
        self.grid_n = 0         # 网格边数
        self.cell_w = 0         # 每格宽度
        self.cell_h = 0         # 每格高度
        self.grid_off_x = 0     # 第 0 条网格线 x 偏移（校准用）
        self.grid_off_y = 0     # 第 0 条网格线 y 偏移（校准用）
        self.grid_calibrated = False  # 是否由网格线校准得到（否则等比均分）
        self.cells = []         # [(cx, cy), ...] 每格中心坐标
        self.dye_panel_x = 0    # 染料板左上角 x
        self.dye_panel_y = 0    # 染料板左上角 y
        self.dye_panel_w = 0    # 染料板宽度
        self.dye_panel_h = 0    # 染料板高度
        self.success = False
        self.message = ""

    def cell_center(self, row, col):
        """返回指定行列格子的中心坐标"""
        return self.cells[row * self.grid_n + col]

    @property
    def dye_visible_h(self):
        """染料板可见区高度（按 5 行可见估算）"""
        if self.dye_panel_h == 0 or self.dye_panel_w == 0:
            return 0
        return self.dye_panel_h  # 5 行整页可见


class CanvasDetector:
    """MAA 零模板定位器：比例坐标粗定位 + 网格线精校准"""

    # 明日方舟绘图画布固定 24×24 网格
    GRID_N = 24

    # 基准客户区 1280×720（MAA 参考项目采样值）
    BASE_W = 1280
    BASE_H = 720

    # 全部坐标均为「游戏客户区坐标」（相对 1280×720 基准），
    # 与 arknights-pixel-autofill v1.3.0 的 CONFIG 一致。
    CONFIG = {
        # 24×24 画布外边界：左、上、右、下
        "grid_left": 295.0,
        "grid_top": 119.0,
        "grid_right": 856.0,
        "grid_bottom": 680.0,

        # 右侧调色板 4 列中心 x
        "palette_cols": (989.0, 1060.0, 1130.0, 1200.0),

        # 调色板位于最上端时，6 行色块中心 y（本项目可见区取前 5 行）
        "palette_visible_rows": (285.0, 355.0, 425.0, 495.0, 565.0, 635.0),
    }

    def __init__(self):
        # 零模板：不再加载任何模板图片
        self._calib = None      # (offset_x, offset_y, scale_x, scale_y)

    # ── 比例坐标换算（MAA _viewport / _uncalibrated_scaled / _scaled） ──

    def _viewport(self, w, h):
        """返回居中 16:9 游戏视口：在 Unity 客户区内部按比例缩放。"""
        scale = min(w / self.BASE_W, h / self.BASE_H)
        viewport_w = self.BASE_W * scale
        viewport_h = self.BASE_H * scale
        return (w - viewport_w) / 2.0, (h - viewport_h) / 2.0, scale

    def _uncalibrated_scaled(self, x, y, w, h):
        """比例坐标 → 客户区像素（未校准，仅居中视口等比缩放）"""
        viewport_x, viewport_y, scale = self._viewport(w, h)
        return viewport_x + x * scale, viewport_y + y * scale

    def _scaled(self, x, y, w, h):
        """比例坐标 → 客户区像素（优先用网格线校准结果，否则回退未校准）"""
        if self._calib is not None:
            offset_x, offset_y, scale_x, scale_y = self._calib
            return offset_x + x * scale_x, offset_y + y * scale_y
        return self._uncalibrated_scaled(x, y, w, h)

    # ── 网格线检测（MAA _detect_grid_axis，numpy 向量化） ──

    @staticmethod
    def _detect_grid_axis(gray, expected_bounds, vertical):
        """在比例坐标估计附近找 N+1 条网格线（MAA 方法）。

        Args:
            gray: (H, W) uint8 灰度图（客户区截图）
            expected_bounds: (left, top, right, bottom) 未校准比例坐标
            vertical: True 检测竖线（返回 x 坐标），False 检测横线（返回 y 坐标）
        Returns:
            list[int]：长度 N+1 的网格线坐标（升序）
        Raises:
            RuntimeError: 未检测到网格线 / 拟合失败 / 置信度不足
        """
        H, W = gray.shape
        left, top, right, bottom = expected_bounds
        expected_start = left if vertical else top
        expected_end = right if vertical else bottom
        expected_pitch = (expected_end - expected_start) / CanvasDetector.GRID_N
        axis_size = W if vertical else H

        search_start = max(2, round(expected_start - expected_pitch * 1.7))
        search_end = min(axis_size - 3, round(expected_end + expected_pitch * 1.7))
        if search_end <= search_start:
            raise RuntimeError("网格搜索区间无效")

        # ── 二阶差分 + 两侧差分的线响应（向量化） ──
        g = gray.astype(np.float32)
        if vertical:
            # 沿 y 方向求差分：R[y-2, x] = |2g[y,x] - g[y-2,x] - g[y+2,x]| + |g[y-2,x] - g[y+2,x]|
            gm2 = g[:-4, :]
            gp2 = g[4:, :]
            g0 = g[2:-2, :]
            resp = np.abs(2.0 * g0 - gm2 - gp2) + np.abs(gm2 - gp2)  # (H-4, W)
            q0 = max(2, round(top - expected_pitch))
            q1 = min(H - 2, round(bottom + expected_pitch))
            sample_idx = np.arange(q0, q1, 3)
            if sample_idx.size == 0:
                raise RuntimeError("网格线采样区间为空")
            scores = resp[sample_idx - 2, :].sum(axis=0)  # (W,)
        else:
            # 沿 x 方向求差分：R[y, x-2] = |2g[y,x] - g[y,x-2] - g[y,x+2]| + |g[y,x-2] - g[y,x+2]|
            lm2 = g[:, :-4]
            lp2 = g[:, 4:]
            l0 = g[:, 2:-2]
            resp = np.abs(2.0 * l0 - lm2 - lp2) + np.abs(lm2 - lp2)  # (H, W-4)
            q0 = max(2, round(left - expected_pitch))
            q1 = min(W - 2, round(right + expected_pitch))
            sample_idx = np.arange(q0, q1, 3)
            if sample_idx.size == 0:
                raise RuntimeError("网格线采样区间为空")
            scores = resp[:, sample_idx - 2].sum(axis=1)  # (H,)

        # ── 局部最大值基线（MAA local_scores + baseline） ──
        local_scores = np.zeros(axis_size, dtype=np.float32)
        for pos in range(search_start, search_end + 1):
            lo = max(0, pos - 4)
            hi = min(axis_size, pos + 5)
            local_scores[pos] = scores[lo:hi].max()
        baseline_values = local_scores[search_start:search_end + 1]
        baseline_values = baseline_values[baseline_values > 0]
        if baseline_values.size == 0:
            raise RuntimeError("未检测到网格线")
        baseline = float(np.median(baseline_values))

        # ── 等间距拟合（MAA：枚举 start±1.5 pitch、pitch±12%，加权期望外边界） ──
        best = None
        start_min = max(2, round(expected_start - expected_pitch * 1.5))
        start_max = min(axis_size - 3, round(expected_start + expected_pitch * 1.5))
        pitch_min = round(expected_pitch * 0.88 * 20)
        pitch_max = round(expected_pitch * 1.12 * 20)
        n = CanvasDetector.GRID_N
        for pitch_step in range(pitch_min, pitch_max + 1):
            pitch = pitch_step / 20.0
            for start in range(start_min, start_max + 1):
                last = round(start + n * pitch)
                if last >= axis_size - 2:
                    continue
                idx = np.arange(n + 1)
                predicted = np.round(start + idx * pitch).astype(int)
                values = local_scores[predicted]
                proximity = (
                    abs(start - expected_start) / expected_pitch
                    + abs(last - expected_end) / expected_pitch
                    + 4.0 * abs(pitch - expected_pitch) / expected_pitch
                )
                # 周期网格存在大量相位等价拟合；用期望外边界加权，
                # 避免把第二条网格线误当成第一条。
                fit_score = float(values.sum()) - baseline * 60.0 * proximity
                if best is None or fit_score > best[0]:
                    best = (fit_score, start, pitch)

        if best is None:
            raise RuntimeError("网格线拟合失败")
        _, start, pitch = best

        # ── 逐线精定位：在预测位置邻域取响应峰值 ──
        positions = []
        for i in range(n + 1):
            predicted = int(round(start + i * pitch))
            lo = max(2, predicted - 4)
            hi = min(axis_size - 2, predicted + 5)
            seg = scores[lo:hi]
            positions.append(lo + int(np.argmax(seg)))

        if any(b <= a for a, b in zip(positions, positions[1:])):
            raise RuntimeError("网格线顺序异常")
        detected_pitch = (positions[-1] - positions[0]) / n
        residual = max(
            abs(pos - (positions[0] + i * detected_pitch))
            for i, pos in enumerate(positions)
        )
        values = np.array([local_scores[p] for p in positions])
        if float(np.median(values)) < baseline * 0.75 or \
                residual > max(4.0, detected_pitch * 0.25):
            raise RuntimeError(
                f"网格识别置信度不足(score={float(np.median(values)) / max(1.0, baseline):.2f}, "
                f"residual={residual:.1f})"
            )
        return positions

    # ── 检测主流程 ──

    def detect(self, screen_bgr, client_rect=None):
        """执行检测（MAA 零模板方法）。

        Args:
            screen_bgr: 全屏 BGR 截图（ImageGrab 图像坐标，原点在虚拟屏左上角）
            client_rect: 可选，游戏窗口客户区 (x, y, w, h, hwnd)（物理像素，来自 find_game_window）。
                提供时只在客户区内检测 → 消除全屏假阳性，且天然适配 DPI/分辨率；
                不提供则回退全屏检测。
        Returns:
            DetectionResult（坐标统一为「物理屏幕坐标」，可直接用于 SetCursorPos /
                ImageGrab bbox；多显示器时内部已做虚拟屏原点换算）
        """
        if client_rect:
            ox, oy, cw, ch, _hwnd = client_rect
            # 物理坐标 → 截图图像坐标：多显示器下虚拟屏原点可能为负，
            # 直接按物理坐标切片会越界，必须先换算。
            ix, iy = physical_to_image(ox, oy)
            region = screen_bgr[iy:iy + ch, ix:ix + cw]
            if region.size == 0:
                return self._detect_in_region(screen_bgr)
            res = self._detect_in_region(region, cw, ch)
            if res.success:
                res.canvas_x += ix
                res.canvas_y += iy
                res.dye_panel_x += ix
                res.dye_panel_y += iy
                res.cells = [(cx + ix, cy + iy) for cx, cy in res.cells]
            # 统一出口：图像坐标 → 物理坐标
            if res.success:
                self._to_physical(res)
            return res

        res = self._detect_in_region(screen_bgr, screen_bgr.shape[1], screen_bgr.shape[0])
        if res.success:
            self._to_physical(res)
        return res

    @staticmethod
    def _to_physical(res):
        """把检测结果从 ImageGrab 图像坐标换算为物理屏幕坐标（多屏安全）。"""
        res.canvas_x, res.canvas_y = image_to_physical(res.canvas_x, res.canvas_y)
        res.dye_panel_x, res.dye_panel_y = image_to_physical(
            res.dye_panel_x, res.dye_panel_y)
        res.cells = [image_to_physical(cx, cy) for cx, cy in res.cells]

    def _detect_in_region(self, region_bgr, cw, ch):
        """在客户区（或全屏）图像内做 MAA 零模板检测。

        Args:
            region_bgr: BGR 图像（客户区截图）
            cw, ch: 客户区逻辑宽高（用于比例坐标换算）
        Returns:
            DetectionResult（坐标相对传入图像原点）
        """
        result = DetectionResult()
        result.grid_n = self.GRID_N
        self._calib = None

        try:
            gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
        except Exception:
            gray = region_bgr

        cfg = self.CONFIG
        gl, gt = self._uncalibrated_scaled(cfg["grid_left"], cfg["grid_top"], cw, ch)
        gr, gb = self._uncalibrated_scaled(cfg["grid_right"], cfg["grid_bottom"], cw, ch)
        expected = (gl, gt, gr, gb)

        # ── 1. 网格线精校准（失败自动回退比例坐标） ──
        x_lines = y_lines = None
        try:
            x_lines = self._detect_grid_axis(gray, expected, vertical=True)
            y_lines = self._detect_grid_axis(gray, expected, vertical=False)
            # 用真实网格线建立校准映射（scale/offset）
            scale_x = (x_lines[-1] - x_lines[0]) / (cfg["grid_right"] - cfg["grid_left"])
            scale_y = (y_lines[-1] - y_lines[0]) / (cfg["grid_bottom"] - cfg["grid_top"])
            offset_x = x_lines[0] - cfg["grid_left"] * scale_x
            offset_y = y_lines[0] - cfg["grid_top"] * scale_y
            self._calib = (offset_x, offset_y, scale_x, scale_y)
            result.grid_calibrated = True
        except Exception:
            x_lines = y_lines = None
            result.grid_calibrated = False

        # ── 2. 画布矩形与格心 ──
        if x_lines is not None and y_lines is not None:
            canvas_x, canvas_y = float(x_lines[0]), float(y_lines[0])
            canvas_w = float(x_lines[-1] - x_lines[0])
            canvas_h = float(y_lines[-1] - y_lines[0])
        else:
            canvas_x, canvas_y = float(gl), float(gt)
            canvas_w = float(gr - gl)
            canvas_h = float(gb - gt)

        result.canvas_x, result.canvas_y = int(round(canvas_x)), int(round(canvas_y))
        result.canvas_w = int(round(canvas_w))
        result.canvas_h = int(round(canvas_h))
        result.cell_w = canvas_w / result.grid_n
        result.cell_h = canvas_h / result.grid_n
        result.grid_off_x = int(round(canvas_x))
        result.grid_off_y = int(round(canvas_y))

        if x_lines is not None and y_lines is not None:
            # 格心 = 相邻两条真实网格线的中点（MAA _grid_center 主路径）
            result.cells = [
                (int(round((x_lines[c] + x_lines[c + 1]) / 2.0)),
                 int(round((y_lines[r] + y_lines[r + 1]) / 2.0)))
                for r in range(result.grid_n)
                for c in range(result.grid_n)
            ]
        else:
            # 回退：比例坐标等比均分（MAA _grid_center 兜底）
            result.cells = [
                (int(round(canvas_x + (c + 0.5) * result.cell_w)),
                 int(round(canvas_y + (r + 0.5) * result.cell_h)))
                for r in range(result.grid_n)
                for c in range(result.grid_n)
            ]

        # ── 3. 染料板矩形（MAA 比例坐标：4 列中心 x + 前 5 行中心 y） ──
        cols = cfg["palette_cols"]
        rows = cfg["palette_visible_rows"][:5]
        col_w = (cols[-1] - cols[0]) / (len(cols) - 1)
        row_h = (rows[-1] - rows[0]) / (len(rows) - 1)
        dl, dt = self._scaled(cols[0] - col_w / 2.0, rows[0] - row_h / 2.0, cw, ch)
        dr, db = self._scaled(cols[-1] + col_w / 2.0, rows[-1] + row_h / 2.0, cw, ch)
        result.dye_panel_x = int(round(dl))
        result.dye_panel_y = int(round(dt))
        result.dye_panel_w = int(round(dr - dl))
        result.dye_panel_h = int(round(db - dt))

        result.success = True
        result.message = ""
        return result
