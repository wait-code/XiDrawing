"""自动绘图主流程：按颜色分批绘制，支持暂停/停止/进度回调"""
import ctypes
import os
import time
import random
from collections import defaultdict

import cv2
import numpy as np

from .capture import ScreenCapture
from .color_mapper import _rgb_to_lab, _lab_distance
from .screen_info import get_screen_info, get_dpi_scale

# 鼠标虚拟键码
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04


class AutoDrawError(Exception):
    """自动绘制失败（页面状态异常 / 选色超时 / 绘制失败），立即终止并向上抛出。"""


def _is_mouse_pressed():
    """检测是否有鼠标按键被按下（用户手动操作）"""
    return (ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000 or
            ctypes.windll.user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000 or
            ctypes.windll.user32.GetAsyncKeyState(VK_MBUTTON) & 0x8000)


class AutoDrawer:
    """自动绘图编排器"""

    # 染料板布局常量（明日方舟绘图活动固定布局）
    DYE_COLS = 4            # 染料板列数
    DYE_VISIBLE_ROWS = 5    # 可见行数（每页 20 色）
    DYE_TOTAL_ROWS = 10     # 总行数（40 色）
    DYE_PAGE1_ROWS = 5      # colors1.png 的行数（第一页）
    DYE_PAGE2_ROWS = 5      # colors2.png 的行数（第二页）
    COLOR_ERR_THRESHOLD = 50.0  # ΔE 兜底阈值，超过则告警
    DYE_SAMPLE_RATIO = 0.7      # 染料格取样中心占比
    SCREENSHOT_PREP_DELAY = 0.3 # 截图前等待（秒）
    DYE_SELECT_MAX_ATTEMPTS = 6 # 选色闭环最大尝试次数
    DYE_IDENTITY_THRESHOLD = 50.0  # 身份匹配 ΔE 阈值（区分不同染料）
    SELECT_OK_THRESHOLD = 40.0  # 选色强校验通过阈值

    # ── 上下栏分批（ColorId 范围，对应 colors1/colors2 全局索引） ──
    UPPER_COLOR_IDS = (0, 19)   # 第一页 0~19（4×5）
    LOWER_COLOR_IDS = (20, 39)  # 第二页 20~39（4×5）

    # ── 运行防护 ──
    VERIFY_INTERVAL = 60        # 每绘制固定格子数后二次校验页面状态
    VERIFY_MAX_ATTEMPTS = 3     # 页面状态校验最大重试次数
    PAGE_MATCH_RATIO = 0.5      # 染料板颜色序列匹配率低于该值视为页面异常
    PAGE_SCROLL_TIMEOUT = 5     # 滚动后页面状态确认失败的最大重试次数

    def __init__(self, mouse, detector_result, palette, color_mapping,
                 on_progress=None, on_log=None, on_status=None, debug=False):
        """
        Args:
            mouse: HumanMouse 实例
            detector_result: DetectionResult 实例
            palette: DyePalette 实例
            color_mapping: {pattern_color → DyeColor}
            on_progress: callback(done, total)
            on_log: callback(message)
            on_status: callback(status_string)
            debug: 是否输出坐标/验证细节
        """
        self.mouse = mouse
        self.det = detector_result
        self.palette = palette
        self.mapping = color_mapping
        self.on_progress = on_progress or (lambda d, t: None)
        self.on_log = on_log or (lambda m: None)
        self.on_status = on_status or (lambda s: None)
        self.debug = debug

        self._paused = False
        self._stopped = False
        self._dye_scrolled = False  # 染料板是否已下拉
        self._last_selected_target_rgb = None  # 当前已选目标颜色，None 表示无
        self._last_region = None    # 最近一次染料板可见区截图（供身份映射）
        self._capture = ScreenCapture()
        self._drawn_since_verify = 0  # 距上次页面状态校验已绘制的格数

        # 屏幕信息：分辨率 / DPI 缩放 / 多显示器虚拟屏原点
        # 用于坐标换算，保证不同显示器（副屏在主屏左/上、缩放 125%/150% 等）下
        # 计算出的绝对坐标与真实点击位置一致。
        self.screen_info = get_screen_info()
        vx, vy = self.screen_info["virtual_left"], self.screen_info["virtual_top"]
        self._virt_origin = (vx, vy)
        if self.debug:
            self.on_log(
                f"屏幕信息: 虚拟屏原点=({vx},{vy}) "
                f"尺寸={self.screen_info['virtual_width']}×"
                f"{self.screen_info['virtual_height']}, "
                f"显示器数={len(self.screen_info['monitors'])}"
            )

        # 染料板几何参数：colors1.png 为 4×5，可见区为整页 5 行。
        # 检测失败时按 DPI 缩放换算兜底（旧版硬编码 385/477 仅适配 100% 缩放，
        # 高 DPI 设备会整体错位）。模板 colors1.png 在 100% 缩放下为 385×477，
        # 物理像素尺寸 = 基准尺寸 × 缩放。
        scale = get_dpi_scale(0)
        panel_w = self.det.dye_panel_w or int(385 * scale)
        panel_h = self.det.dye_panel_h or int(477 * scale)  # colors1.png 5 行高
        self._dye_cell_w = panel_w / self.DYE_COLS
        self._dye_cell_h = panel_h / self.DYE_PAGE1_ROWS  # 单格真实高度
        self._dye_click_margin = 0.2  # 点击点限制在 cell 中心 60% 区域内

    @staticmethod
    def run_with_dpi_check(detect_fn=None, on_log=None):
        """DPI 检测前置执行入口：先检测环境 → 计算真实坐标 → 再执行后续绘制。

        适配所有设备：无论系统 DPI 缩放 / 分辨率如何，统一先收集 DPI profile
        （缩放、物理/逻辑分辨率、虚拟屏原点），再执行画布检测并返回结果，
        保证后续所有点击/截图坐标均为物理像素且与真实屏幕对齐。

        Args:
            detect_fn: 可选 callable，接收 (screen_bgr, client_rect) 返回
                DetectionResult；缺省时内部使用 CanvasDetector 默认流程。
            on_log: 可选日志回调。

        Returns:
            (result, screen_bgr, client_rect, profile) — 检测结果（坐标已换算为
            物理屏幕坐标）、全屏截图、窗口客户区（可能为 None）、DPI profile。
        """
        from .detector import CanvasDetector, find_game_window
        log = on_log or (lambda m: None)

        # 1) DPI 检测：物理/逻辑分辨率、缩放、虚拟屏原点
        from .screen_info import get_dpi_profile
        profile = get_dpi_profile()
        pw, ph = profile["physical"]
        lw, lh = profile["logical"]
        log(f"DPI 检测: 缩放 {profile['scale']:.0%}，物理 {pw}×{ph}，"
            f"逻辑 {lw}×{lh}，显示器 {profile['monitor_count']} 台，"
            f"虚拟屏原点 ({profile['virtual_left']},{profile['virtual_top']})")

        # 2) 截图 + 窗口锚定（物理像素坐标系）
        cap = ScreenCapture()
        screen = cap.grab_full()
        client_rect = find_game_window()
        if client_rect:
            log(f"已锚定游戏窗口客户区: {client_rect[2]}×{client_rect[3]} "
                f"@({client_rect[0]},{client_rect[1]})")
        else:
            log("⚠ 未找到游戏窗口(标题含'明日方舟')，回退全屏检测")

        # 3) 画布检测：坐标统一为物理屏幕像素
        detector = CanvasDetector()
        if detect_fn is not None:
            result = detect_fn(screen, client_rect)
        else:
            result = detector.detect(screen, client_rect)
        if result.success:
            log(f"画布定位: ({result.canvas_x},{result.canvas_y}) "
                f"{result.canvas_w}×{result.canvas_h}，"
                f"网格 {result.grid_n}×{result.grid_n}，"
                f"染料板 ({result.dye_panel_x},{result.dye_panel_y})")
        else:
            log(f"❌ 检测失败: {result.message}")
        return result, screen, client_rect, profile

    def pause(self):
        self._paused = True
        self.on_log("⏸ 已暂停，按继续恢复...")

    def resume(self):
        self._paused = False
        self.on_log("▶ 继续绘制...")

    def stop(self):
        self._stopped = True
        self.mouse.stop()
        self.on_log("⏹ 已停止")

    def _wait_if_paused(self):
        """暂停时阻塞等待"""
        while self._paused and not self._stopped:
            time.sleep(0.1)

    def _check_user_interrupt(self):
        """检测用户手动干预（鼠标点击或已标记暂停/停止），若检测到则自动暂停"""
        if self._stopped:
            return True
        if _is_mouse_pressed():
            self._paused = True
            self.on_log("🖱 检测到鼠标点击，自动暂停")
            return True
        return self._paused

    def _dye_global_row(self, dye):
        """计算染料在游戏面板中的全局行号（0~9）"""
        if dye.page == 1:
            return dye.row
        else:
            return self.DYE_PAGE1_ROWS + dye.row

    def _ensure_dye_visible(self, dye):
        """确保目标染料在面板中可见（委托给 _ensure_page，按 page 而非全局行判断，消除状态打架）"""
        return self._ensure_page(dye.page)

    def _ensure_page(self, page):
        """确保染料板处于指定页（page1=顶部不滚，page2=下拉），返回是否就绪"""
        want_scrolled = (page == 2)
        if want_scrolled != self._dye_scrolled:
            self._scroll_dye_panel(down=want_scrolled)
            if self._stopped:
                return False
        return True

    def _scroll_dye_panel(self, down=True):
        """拖拽滚动染料板（游戏不响应滚轮，需左键按住向上/下拉）

        down=True: 向上拖，把 page 2 拉上来（拖 4 行距离，游戏需一次以上拖拽）
        down=False: 向下拖，滚回 page 1
        """
        anchor_x = self.det.dye_panel_x + self.det.dye_panel_w // 2
        anchor_y = self.det.dye_panel_y + int(self._dye_cell_h * self.DYE_VISIBLE_ROWS) // 2
        # 拖拽 5 行高度（整页高度，游戏惯性大）
        dist = int(self._dye_cell_h * 5)

        if down:
            self.mouse.drag_scroll(anchor_x, anchor_y, dist, down=True)
            self._dye_scrolled = True
            if self._redetect_visible_dyes(scrolled=True):
                self.on_log("  ⬇ 染料板下拉至 page 2")
            else:
                self._dye_scrolled = False
                self.on_log("  ⚠ 染料板下拉后重检测失败")
                raise AutoDrawError(
                    "染料板下拉后重检测失败（识别超时/页面状态异常），已终止绘制"
                )
        else:
            self.mouse.drag_scroll(anchor_x, anchor_y, dist, down=False)
            self._dye_scrolled = False
            if self._redetect_visible_dyes(scrolled=False):
                self.on_log("  ⬆ 染料板滚回 page 1")
            else:
                self._dye_scrolled = True
                self.on_log("  ⚠ 染料板滚回后重检测失败")
                raise AutoDrawError(
                    "染料板滚回后重检测失败（识别超时/页面状态异常），已终止绘制"
                )

        self._ensure_cursor_in_game()
        self.mouse.random_pause(0.08, 0.16)

    def _ensure_cursor_in_game(self):
        """滚动后检查鼠标是否仍在游戏区域内，不在则移回并重新检测"""
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))

        # 游戏区域 = 画布 + 染料板 的外接矩形
        min_x = min(self.det.canvas_x, self.det.dye_panel_x)
        max_x = max(self.det.canvas_x + self.det.canvas_w,
                    self.det.dye_panel_x + self.det.dye_panel_w)
        min_y = min(self.det.canvas_y, self.det.dye_panel_y)
        max_y = max(self.det.canvas_y + self.det.canvas_h,
                    self.det.dye_panel_y + int(self._dye_cell_h * 10))

        in_game = min_x <= point.x <= max_x and min_y <= point.y <= max_y

        if not in_game:
            self.on_log("  ⚠ 鼠标滑出游戏区域，正在移回并重新检测...")
            # 移到染料板可见区中心
            safe_x = self.det.dye_panel_x + self.det.dye_panel_w // 2
            safe_y = self.det.dye_panel_y + int(self._dye_cell_h *
                                                 self.DYE_VISIBLE_ROWS) // 2
            ctypes.windll.user32.SetCursorPos(safe_x, safe_y)
            time.sleep(0.2)
            if self._redetect_visible_dyes(scrolled=self._dye_scrolled):
                self.on_log("  ✓ 鼠标已移回游戏区域")
            else:
                self.on_log("  ⚠ 移回后重检测失败")

    def _redetect_visible_dyes(self, scrolled):
        """截图染料板可见区域，刷新对应染料的屏幕实时颜色

        Returns:
            bool: 是否成功刷新
        """
        try:
            cap = ScreenCapture()
            px, py = self.det.dye_panel_x, self.det.dye_panel_y
            pw = self.det.dye_panel_w
            visible_h = int(self._dye_cell_h * self.DYE_VISIBLE_ROWS)

            region = cap.grab_region((px, py, px + pw, py + visible_h))
            if region is not None and region.size > 0:
                self.palette.update_visible_dyes(region, scrolled=scrolled)
                self._last_region = region
                self.on_log("  ✓ 可见染料颜色已刷新")
                return True
        except Exception as e:
            self.on_log(f"  ⚠ 刷新染料颜色异常: {e}")
        return False

    # ═══════════════ 染料格几何定位 ═══════════════

    def _get_dye_cell_center_legacy(self, visible_row, col):
        """【已废弃】用固定几何计算染料格中心，由 test_click_dye 专用

        colors1.png 为 4×5，对应第一页 5 行；第二页下拉后同样为 5 行。
        因此单格高度 = template_h / 5。
        """
        if visible_row < 0 or visible_row >= self.DYE_VISIBLE_ROWS:
            return None

        cell_w = self._dye_cell_w
        cell_h = self._dye_cell_h
        margin = self._dye_click_margin

        # 在 cell 内中心 60% 区域随机取点，模拟人眼也不总点正中心
        half_w = cell_w * (1 - margin * 2) / 2
        half_h = cell_h * (1 - margin * 2) / 2
        dx = random.uniform(-half_w, half_w)
        dy = random.uniform(-half_h, half_h)

        cx = self.det.dye_panel_x + col * cell_w + cell_w / 2 + dx
        cy = self.det.dye_panel_y + visible_row * cell_h + cell_h / 2 + dy
        return int(round(cx)), int(round(cy))

    def _calibrate_scroll_rows(self, region):
        """通过颜色序列对齐，确定当前可见区起始全局行（scroll_rows）。

        染料板布局固定（4 列 × 10 行，row-major 连续编号 0..39）。
        即使显示器色偏导致个别格身份识别偏差，用滑动窗口「最大重叠」仍能
        稳健定位滚动偏移，进而支持精确几何点击——几何定位不依赖颜色识别，
        天然免疫显示器色偏与拖拽滚动的亚格偏移。
        Returns:
            int: scroll_rows（0=顶部, 1/2=下拉后起始全局行）
        """
        h, w = region.shape[:2]
        cell_h = h / self.DYE_VISIBLE_ROWS
        cell_w = w / self.DYE_COLS
        margin = (1.0 - self.DYE_SAMPLE_RATIO) / 2
        vis = []
        for r in range(self.DYE_VISIBLE_ROWS):
            for c in range(self.DYE_COLS):
                cx0 = int(c * cell_w + cell_w * margin)
                cy0 = int(r * cell_h + cell_h * margin)
                cx1 = int((c + 1) * cell_w - cell_w * margin)
                cy1 = int((r + 1) * cell_h - cell_h * margin)
                patch = region[cy0:cy1, cx0:cx1]
                if patch.size == 0:
                    vis.append(-1)
                    continue
                avg = patch.mean(axis=(0, 1))
                rgb = (int(round(avg[2])), int(round(avg[1])), int(round(avg[0])))
                dye = self.palette.nearest_by_base(rgb)
                vis.append(dye.index if dye else -1)

        best_off, best_match = 0, -1
        n_cells = self.DYE_VISIBLE_ROWS * self.DYE_COLS
        for off in range(0, self.DYE_TOTAL_ROWS - self.DYE_VISIBLE_ROWS + 1):
            match = 0
            for i in range(n_cells):
                gr = off + i // self.DYE_COLS
                gc = i % self.DYE_COLS
                gidx = gr * self.DYE_COLS + gc
                if vis[i] == gidx:
                    match += 1
            if match > best_match:
                best_match, best_off = match, off
        return best_off

    def _page_match_ratio(self, region):
        """计算染料板可见区与「当前期望页面」颜色序列的匹配率 [0,1]。

        复用 _calibrate_scroll_rows 的颜色序列对齐思路：逐个可见格取平均色，
        用 palette 基准色表识别其 ColorId，统计与「期望全局序列」的重合比例。
        匹配率低 → 说明页面被弹窗遮挡 / 误切页面 / 染料板滚动异常。

        Returns:
            float: 匹配率；region 无效时返回 0.0
        """
        if region is None or region.size == 0:
            return 0.0
        h, w = region.shape[:2]
        if h == 0 or w == 0:
            return 0.0
        cell_h = h / self.DYE_VISIBLE_ROWS
        cell_w = w / self.DYE_COLS
        margin = (1.0 - self.DYE_SAMPLE_RATIO) / 2
        matched = 0
        total = 0
        for r in range(self.DYE_VISIBLE_ROWS):
            for c in range(self.DYE_COLS):
                cx0 = int(c * cell_w + cell_w * margin)
                cy0 = int(r * cell_h + cell_h * margin)
                cx1 = int((c + 1) * cell_w - cell_w * margin)
                cy1 = int((r + 1) * cell_h - cell_h * margin)
                patch = region[cy0:cy1, cx0:cx1]
                if patch.size == 0:
                    continue
                avg = patch.mean(axis=(0, 1))
                rgb = (int(round(avg[2])), int(round(avg[1])), int(round(avg[0])))
                dye = self.palette.nearest_by_base(rgb)
                if dye is None:
                    continue
                total += 1
                scroll_rows = (self.DYE_TOTAL_ROWS - self.DYE_VISIBLE_ROWS) \
                    if self._dye_scrolled else 0
                expected_gidx = (scroll_rows + r) * self.DYE_COLS + c
                if expected_gidx == dye.index:
                    matched += 1
        if total == 0:
            return 0.0
        return matched / total

    def _verify_page_state(self, context=""):
        """运行防护：二次校验页面状态（防止弹窗/误切页面导致乱点）。

        截取染料板可见区，按颜色序列匹配率判断页面是否仍处于绘制界面；
        匹配率达标即恢复计数，不达标则短暂等待后重试；重试耗尽立即终止任务
        并向上抛出 AutoDrawError。

        Raises:
            AutoDrawError: 页面状态异常且重试后仍未恢复
        """
        if self._stopped:
            return
        for attempt in range(1, self.VERIFY_MAX_ATTEMPTS + 1):
            self._wait_if_paused()
            if self._stopped:
                return
            cap = ScreenCapture()
            px, py = self.det.dye_panel_x, self.det.dye_panel_y
            pw = self.det.dye_panel_w
            visible_h = int(self._dye_cell_h * self.DYE_VISIBLE_ROWS)
            region = cap.grab_region((px, py, px + pw, py + visible_h))
            ratio = self._page_match_ratio(region)
            self.on_log(
                f"  🛡 页面状态校验[{context}] 第{attempt}次: "
                f"匹配率 {ratio:.0%} (阈值 {self.PAGE_MATCH_RATIO:.0%})"
            )
            if ratio >= self.PAGE_MATCH_RATIO:
                self._drawn_since_verify = 0
                return
            if attempt < self.VERIFY_MAX_ATTEMPTS:
                self.on_log("  ⚠ 页面状态可疑，等待后重试...")
                time.sleep(0.8)
        raise AutoDrawError(
            f"页面状态异常{('（' + context + '）') if context else ''}："
            f"染料板匹配率持续低于 {self.PAGE_MATCH_RATIO:.0%}，"
            "疑似弹窗遮挡或页面被切换，已终止绘制以保护画布"
        )

    def _check_select_success(self, dye, target_rgb):
        """选色后的确认：截图当前选中格，强校验颜色是否匹配目标。

        用于选色超时/失败判定：返回 True 表示选中成功；False 表示选色异常，
        由调用方决定重试或终止。
        """
        try:
            grow = self._dye_global_row(dye)
            scroll_rows = (self.DYE_TOTAL_ROWS - self.DYE_VISIBLE_ROWS) \
                if self._dye_scrolled else 0
            rel = grow - scroll_rows
            if rel < 0 or rel >= self.DYE_VISIBLE_ROWS:
                return False
            h, w = self._last_region.shape[:2]
            cell_w = w / self.DYE_COLS
            cell_h = h / self.DYE_VISIBLE_ROWS
            click_x = int(self.det.dye_panel_x + dye.col * cell_w + cell_w / 2)
            click_y = int(self.det.dye_panel_y + rel * cell_h + cell_h / 2)
            err = self._verify_dye_selected(click_x, click_y, target_rgb)
            return err <= self.SELECT_OK_THRESHOLD
        except Exception:
            return False

    def _verify_dye_selected(self, x, y, target_rgb):
        """验证染料格点击后中心区域颜色是否与目标匹配

        Args:
            x, y: 点击坐标（用于截取周边验证区）
            target_rgb: (R, G, B) 目标颜色

        Returns:
            float: LAB ΔE，异常时返回 0.0
        """
        try:
            size = 28
            x0 = max(0, x - size // 2)
            y0 = max(0, y - size // 2)
            x1 = x0 + size
            y1 = y0 + size
            after_patch = self._capture.grab_region((x0, y0, x1, y1))
            if after_patch is None or after_patch.size == 0:
                return 0.0

            after_mean = after_patch.mean(axis=(0, 1))
            actual_rgb = tuple(np.round(after_mean[::-1]).astype(int))  # BGR→RGB
            lab_expected = _rgb_to_lab(target_rgb)
            lab_actual = _rgb_to_lab(actual_rgb)
            color_err = _lab_distance(lab_expected, lab_actual)

            if self.debug:
                self.on_log(f"     选色验证: 目标={target_rgb} 实际={actual_rgb} ΔE={color_err:.1f}")

            return color_err
        except Exception as e:
            if self.debug:
                self.on_log(f"     选色验证异常: {e}")
            return 0.0

    def _find_best_visible_dye(self, region, target_rgb):
        """在可见染料区截图中逐格取色，找到最接近 target_rgb 的格

        Args:
            region: 染料板可见区截图 (H, W, 3) BGR numpy array
            target_rgb: (R, G, B) 目标颜色

        Returns:
            (best_r, best_c, best_dist) 或 (None, None, inf)
        """
        h, w = region.shape[:2]
        cell_h_vis = h / self.DYE_VISIBLE_ROWS
        cell_w_vis = w / self.DYE_COLS
        sample_margin = (1.0 - self.DYE_SAMPLE_RATIO) / 2

        target_lab = _rgb_to_lab(target_rgb)
        best_r, best_c = None, None
        best_dist = float('inf')

        for r in range(self.DYE_VISIBLE_ROWS):
            for c in range(self.DYE_COLS):
                cx0 = int(c * cell_w_vis + cell_w_vis * sample_margin)
                cy0 = int(r * cell_h_vis + cell_h_vis * sample_margin)
                cx1 = int((c + 1) * cell_w_vis - cell_w_vis * sample_margin)
                cy1 = int((r + 1) * cell_h_vis - cell_h_vis * sample_margin)
                patch = region[cy0:cy1, cx0:cx1]
                if patch.size == 0:
                    continue
                avg = patch.mean(axis=(0, 1))
                rgb = tuple(np.round(avg[::-1]).astype(int))  # BGR→RGB
                dist = _lab_distance(target_lab, _rgb_to_lab(rgb))
                if dist < best_dist:
                    best_dist = dist
                    best_r, best_c = r, c

        return best_r, best_c, best_dist

    def _select_dye(self, dye, target_rgb):
        """选择染料（几何定位闭环）：校准滚动偏移 → 几何点击 → 弱校验

        根治历史三类问题：
          - page 滚动状态打架（_ensure_page 按 dye.page 统一判定，消除与 draw() 的双重逻辑）
          - 拖拽滚动亚格偏移（_calibrate_scroll_rows 用颜色序列对齐确定偏移量）
          - 显示器色偏（几何点击不依赖颜色识别，天然免疫；序列对齐对色偏鲁棒）
        """
        if self._stopped:
            return False
        if target_rgb == self._last_selected_target_rgb:
            return True

        # 用基准身份色确定"真正要选的染料"（仅用于定位，不用于颜色校验）
        best_dye = self.palette.nearest_by_base(target_rgb) or dye

        last_reason = ""
        for attempt in range(self.DYE_SELECT_MAX_ATTEMPTS):
            if self._stopped:
                return False
            self._wait_if_paused()
            if self._stopped:
                return False

            # 1) 确保在目标页（page1 不滚 / page2 下拉）
            if not self._ensure_page(best_dye.page):
                if self._stopped:
                    return False
                last_reason = "无法切换染料板页面"
                break

            # 2) 截图可见区并刷新实时色
            if not self._redetect_visible_dyes(self._dye_scrolled):
                self.mouse.random_pause(0.06, 0.12)
                last_reason = "可见染料区截图/识别失败"
                continue
            region = self._last_region
            if region is None:
                last_reason = "可见染料区为空"
                continue

            # 3) 翻页后可见起始全局行由滚动状态决定（非颜色校准）
            #    游戏面板固定 4×10、可见 5 行，下拉到底偏移恒为 5；
            #    用状态而非颜色序列对齐，避免色差导致偏移算错、点击飞出面板。
            scroll_rows = (self.DYE_TOTAL_ROWS - self.DYE_VISIBLE_ROWS) if self._dye_scrolled else 0
            g_row = self._dye_global_row(best_dye)
            rel = g_row - scroll_rows
            if rel < 0 or rel >= self.DYE_VISIBLE_ROWS:
                # 目标不在可见区 → 仅滚动一次后重试（不再无谓二次滚动）
                self.on_log(f"  目标染料 {best_dye.index} 不在可见区(偏移{scroll_rows})，滚动进入...")
                self._scroll_dye_panel(down=(best_dye.page == 2))
                self.mouse.random_pause(0.1, 0.18)
                last_reason = "目标染料不在可见区"
                continue

            # 4) 几何点击目标格（布局固定，免疫色偏）+ 越界钳制，绝不点出面板
            h, w = region.shape[:2]
            cell_w = w / self.DYE_COLS
            cell_h = h / self.DYE_VISIBLE_ROWS
            rel = max(0, min(self.DYE_VISIBLE_ROWS - 1, rel))
            click_x = int(self.det.dye_panel_x + best_dye.col * cell_w + cell_w / 2)
            click_y = int(self.det.dye_panel_y + rel * cell_h + cell_h / 2)
            self.on_log(f"  选色: 目标RGB{target_rgb} → 染料{best_dye.index} (页{best_dye.page} 可见行{rel})")
            self.mouse.click(click_x, click_y)
            self.mouse.random_pause(0.06, 0.12)

            # 5) 弱校验（仅日志；几何定位已保证点中正确格）
            err = self._verify_dye_selected(click_x, click_y, target_rgb)
            self._last_selected_target_rgb = target_rgb
            self.on_log(f"  ✓ 选色完成 染料{best_dye.index} (校验ΔE={err:.1f})")
            return True

        # 选色失败/识别超时：立即终止任务，向上层抛出异常
        if self._stopped:
            return False
        raise AutoDrawError(
            f"选色失败（{last_reason or '未知原因'}）："
            f"染料 {best_dye.index} (页{best_dye.page}) 尝试 "
            f"{self.DYE_SELECT_MAX_ATTEMPTS} 次仍无法选中，已终止绘制以保护画布"
        )

    def _draw_batch(self, indices, color, dye, gn):
        """用同一染料绘制一批格子。

        同一行内列号相邻的连续格子用拖拽滑动（按住左键依次滑过），
        孤立格子仍然逐格点击。
        """
        if self._stopped:
            return 0, 0

        drawn = 0
        skipped = 0
        i = 0
        total = len(indices)

        while i < total and not self._stopped:
            # 暂停/停止/用户干预检测
            self._wait_if_paused()
            if self._stopped:
                break
            if self._check_user_interrupt():
                self._wait_if_paused()
                if self._stopped:
                    break

            # ── 收集同行相邻的连续格子 ──
            start = i
            while i + 1 < total:
                r1, c1 = indices[i] // gn, indices[i] % gn
                r2, c2 = indices[i + 1] // gn, indices[i + 1] % gn
                if r1 == r2 and c2 == c1 + 1:
                    i += 1
                else:
                    break
            i += 1  # i 现在是该组「之后」第一个索引
            group = indices[start:i]
            n_group = len(group)

            # ── 绘制 ──
            if n_group == 1:
                # 孤立格子：常规点击
                idx = group[0]
                row, col = idx // gn, idx % gn
                cx, cy = self.det.cell_center(row, col)
                try:
                    self.mouse.click(cx, cy)
                except Exception as e:
                    raise AutoDrawError(f"格子({row},{col})点击失败: {e}") from e
                drawn += 1
                self.on_status(f"绘制 {self._done + drawn}/{self._total} — 行{row} 列{col}")
            else:
                # 连续格子：拖拽滑动
                points = []
                for idx in group:
                    row, col = idx // gn, idx % gn
                    cx, cy = self.det.cell_center(row, col)
                    points.append((cx, cy))
                try:
                    self.mouse.drag_draw(points)
                except Exception as e:
                    raise AutoDrawError(f"格子滑动绘制失败(行{row}): {e}") from e
                drawn += n_group
                last_idx = group[-1]
                row, col = last_idx // gn, last_idx % gn
                self.on_status(f"绘制 {self._done + drawn}/{self._total} — 行{row} 列{col} (滑动 {n_group} 格)")

            # 每完成一行停顿一下
            if n_group > 0:
                col = group[-1] % gn
                if col == gn - 1:
                    self.mouse.random_pause(0.08, 0.18)

            # 运行防护：每绘制固定格子数量后二次校验页面状态（防弹窗/误切页面乱点）
            self._drawn_since_verify += n_group
            if self._drawn_since_verify >= self.VERIFY_INTERVAL:
                self._verify_page_state("已绘制" + str(self._done + drawn) + "格")

        return drawn, skipped

    def test_click_dye(self, dye_index=0):
        """测试点击：移动并点击指定染料格，用于验证键鼠控制是否生效"""
        if not self.palette.dyes:
            self.on_log("❌ 没有提取到染料，无法测试")
            return False

        dye = self.palette.dyes[min(dye_index, len(self.palette.dyes) - 1)]
        self.on_log(f"🧪 测试点击染料 {dye.index} RGB{dye.rgb}")

        # 确保染料可见
        self._ensure_dye_visible(dye)

        grow = self._dye_global_row(dye)
        gcol = dye.col
        scroll_offset = (self.DYE_TOTAL_ROWS - self.DYE_VISIBLE_ROWS) if self._dye_scrolled else 0
        visible_row = grow - scroll_offset

        center = self._get_dye_cell_center_legacy(visible_row, gcol)
        if center is None:
            self.on_log("❌ 计算染料格坐标失败")
            return False

        dx, dy = center
        self.on_log(f"  即将移动到 ({dx}, {dy}) 并点击，请观察光标是否到达")
        self.mouse.click(dx, dy)
        self.on_log(f"✅ 测试点击完成，当前光标应在染料格附近")
        return True

    def draw(self, pattern_pixels):
        """执行自动绘图：按颜料色板上下栏分两批次绘制。

        色板固定 40 色：ColorId 0~23 为上栏、24~39 为下栏。
        批次1（上栏）：滑动色板至顶部，遍历 0~23 全部颜色；
        批次2（下栏）：向下滑动色板到底部，遍历 24~39 全部颜色。
        每个批次内按 ColorId 升序遍历，配合 _draw_batch 的同行相邻合并拖拽、
        零散单点，以及运行防护（固定格数后校验页面状态，失败抛 AutoDrawError）。
        """
        gn = self.det.grid_n
        self._total = len(pattern_pixels)
        self._done = 0
        self._drawn_since_verify = 0

        self.on_log(f"开始绘制 {gn}×{gn} = {self._total} 格")

        if not self.det.success:
            raise AutoDrawError("检测结果无效，无法绘制")
        if not self.palette.dyes:
            raise AutoDrawError("没有提取到染料，无法绘制")

        # 按颜色分组
        color_groups = defaultdict(list)
        for idx, color in enumerate(pattern_pixels):
            color_groups[color].append(idx)

        # 过滤白色/近白色色块（背景色，无需绘制）
        white_skipped = 0
        for color in list(color_groups.keys()):
            if all(c > 230 for c in color):
                white_skipped += len(color_groups.pop(color))
        if white_skipped:
            self.on_log(f"⚪ 跳过白色色块 {white_skipped} 格")
            self._done += white_skipped
            self._total -= white_skipped

        self.on_log(f"共 {len(color_groups)} 种有效颜色")

        # 按 ColorId 分页：0~19 第一页 / 20~39 第二页
        upper_batches = []  # [(color, indices, dye)]
        lower_batches = []
        unmatched = []

        for color, indices in color_groups.items():
            dye = self.mapping.get(color)
            if dye is None:
                unmatched.append((color, indices))
                continue
            if self.UPPER_COLOR_IDS[0] <= dye.index <= self.UPPER_COLOR_IDS[1]:
                upper_batches.append((color, indices, dye))
            elif self.LOWER_COLOR_IDS[0] <= dye.index <= self.LOWER_COLOR_IDS[1]:
                lower_batches.append((color, indices, dye))
            else:
                # 索引越界兜底：按 page 归入对应批次
                if dye.page == 1:
                    upper_batches.append((color, indices, dye))
                else:
                    lower_batches.append((color, indices, dye))

        # 每个批次内按 ColorId 升序遍历（保证稳定顺序）
        upper_batches.sort(key=lambda b: b[2].index)
        lower_batches.sort(key=lambda b: b[2].index)

        stats = {"total": self._total, "drawn": 0, "skipped": 0, "colors": len(color_groups)}

        # 未匹配颜色
        for color, indices in unmatched:
            self.on_log(f"⚠ 颜色 {color} 无匹配染料，跳过 {len(indices)} 格")
            stats["skipped"] += len(indices)
            self._done += len(indices)
            self.on_progress(self._done, self._total)

        def _draw_batch_section(title, batches):
            """绘制一个批次（上栏或下栏）"""
            self.on_log(f"▶ {title}，{len(batches)} 种颜色")
            for color, indices, dye in batches:
                if self._stopped:
                    break
                self._wait_if_paused()
                if self._stopped:
                    break

                self.on_status(f"选色 RGB{color} → 染料{dye.index}")
                # 选色失败/识别超时由 _select_dye 抛 AutoDrawError 终止任务
                if not self._select_dye(dye, color):
                    if self._stopped:
                        break
                    continue

                # 选色完成后稍作停顿，让玩家看到颜色已切换，再开始绘制
                self.mouse.random_pause(0.1, 0.2)
                drawn, _ = self._draw_batch(indices, color, dye, gn)
                stats["drawn"] += drawn
                self._done += len(indices)
                self.on_progress(self._done, self._total)

        # ── 批次 1：第一页 ColorId 0~19，滑动色板至顶部 ──
        if upper_batches:
            self._ensure_page(1)  # 滑动色板至顶部（若已下拉则滚回）
            self._verify_page_state("批次1 第一页开始")
            _draw_batch_section("批次1 第一页 ColorId 0~19", upper_batches)

        # ── 批次 2：第二页 ColorId 20~39，向下滑动色板到底部 ──
        if lower_batches and not self._stopped:
            self._ensure_page(2)  # 向下滑动色板到底部
            self._verify_page_state("批次2 第二页开始")
            _draw_batch_section("批次2 第二页 ColorId 20~39", lower_batches)

        if self._stopped:
            self.on_log(f"⏹ 绘制中止，已完成 {self._done}/{self._total}")
        else:
            # 绘制结束滚回顶部，保持界面整洁（收尾失败不阻断完成）
            try:
                if self._dye_scrolled:
                    self._scroll_dye_panel(down=False)
            except AutoDrawError as e:
                self.on_log(f"  ⚠ 收尾滚回顶部失败: {e}")
            self.on_log(f"✅ 绘制完成！共 {self._done} 格，绘制 {stats['drawn']} 格，跳过 {stats['skipped']} 格")

        return stats
