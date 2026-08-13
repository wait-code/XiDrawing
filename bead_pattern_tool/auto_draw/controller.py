"""人类化键鼠控制 — 参考 MaaFramework 的 Win32 输入实现

核心思路：
1. 移动：使用 SetCursorPos 沿贝塞尔路径逐步移动（可靠、精确、不受 DPI 缩放影响）
2. 点击：支持多种方案应对不同游戏的输入屏蔽：
   - sendinput:  SendInput API（微软官方推荐，mouse_event 的现代替代）
   - mouse_event: 旧版 mouse_event API（兼容性好，部分老游戏适用）
   - pydirectinput: pydirectinput 库（专为 DirectX 游戏设计）
   - postmessage:  PostMessage 向窗口句柄发 WM_LBUTTONDOWN/UP（绕过输入队列）
3. 默认速度显著放慢，每一步肉眼可见，便于玩家观察和排查
4. 移动到位后、点击前后都有可见停顿，确保游戏/玩家能感知
"""
import time
import random
import math
import ctypes
import ctypes.wintypes

# 尝试导入 pydirectinput（可选依赖，未安装时该方案不可用）
try:
    import pydirectinput as _pdi
    _HAS_PDI = True
except ImportError:
    _HAS_PDI = False

# ── DPI / 坐标辅助 ──


def _make_dpi_aware():
    """将当前进程设为 DPI aware，使 GetSystemMetrics / SendInput 使用物理像素。

    优先 PER_MONITOR(2)（与 capture.py / screen_info.py 一致，多显示器
    混合缩放时各屏坐标均正确），失败降级 SYSTEM(1)，再降级旧 API。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # SYSTEM_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


# 在导入时尝试开启 DPI aware
_make_dpi_aware()


# ── Win32 常量 ──
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
SM_CXSCREEN = 0
SM_CYSCREEN = 1

# SendInput 相关
INPUT_MOUSE = 0

# PostMessage 相关
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEWHEEL = 0x020A
MK_LBUTTON = 0x0001
WHEEL_DELTA_SCROLLED = 120


# ── SendInput 结构定义 ──

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


# ── 底层函数 ──


def _get_cursor_pos():
    point = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def _set_cursor_pos(x, y):
    """直接设置光标位置（屏幕物理像素）"""
    ctypes.windll.user32.SetCursorPos(int(round(x)), int(round(y)))


def _mouse_event_click(down=True):
    """在当前光标位置发送鼠标按下/释放事件（旧版 API，兼容性好）"""
    flags = MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
    ctypes.windll.user32.mouse_event(flags, 0, 0, 0, 0)


def _sendinput_click(down=True):
    """使用 SendInput API 发送鼠标按下/释放事件

    SendInput 是微软官方推荐替代 mouse_event 的方案，
    生成与真实硬件设备一致的 INPUT 结构，穿透性更强。
    """
    flags = MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
    extra = ctypes.pointer(ctypes.wintypes.ULONG(0))
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(0, 0, 0, flags, 0, extra)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _sendinput_wheel(delta):
    """使用 SendInput 发送滚轮事件"""
    extra = ctypes.pointer(ctypes.wintypes.ULONG(0))
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(0, 0, delta * WHEEL_DELTA, MOUSEEVENTF_WHEEL, 0, extra)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _pydirectinput_click(down=True):
    """使用 pydirectinput 库发送点击（需 pip install pydirectinput）"""
    if down:
        _pdi.mouseDown(button="left")
    else:
        _pdi.mouseUp(button="left")


def _pydirectinput_wheel(delta):
    """使用 pydirectinput 发送滚轮"""
    _pdi.scroll(-delta if delta < 0 else delta)


def _get_foreground_window():
    """获取当前前台窗口句柄"""
    return ctypes.windll.user32.GetForegroundWindow()


def _screen_to_client(hwnd, x, y):
    """屏幕坐标转窗口客户区坐标"""
    point = ctypes.wintypes.POINT(x, y)
    ctypes.windll.user32.ScreenToClient(hwnd, ctypes.byref(point))
    return point.x, point.y


def _postmessage_click(hwnd, x, y, down=True):
    """通过 PostMessage 向窗口句柄发送鼠标点击消息

    直接向窗口消息队列投递 WM_LBUTTONDOWN/UP，
    绕过硬件输入队列。坐标需为客户区坐标。
    """
    # 屏幕坐标转客户区坐标
    cx, cy = _screen_to_client(hwnd, x, y)
    lparam = (cy << 16) | (cx & 0xFFFF)
    if down:
        ctypes.windll.user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    else:
        ctypes.windll.user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)


def _send_mouse_absolute(x, y):
    """SendInput 绝对坐标移动（备用方案，当前未使用）"""
    sw = max(ctypes.windll.user32.GetSystemMetrics(SM_CXSCREEN), 1)
    sh = max(ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN), 1)
    ax = int((int(x) * 65535) / sw)
    ay = int((int(y) * 65535) / sh)
    ctypes.windll.user32.mouse_event(
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay, 0, 0
    )


def _sleep_until(target_perf):
    """MAA 式精确休眠: sleep_until 而非 sleep_for，避免累积漂移"""
    remaining = target_perf - time.perf_counter()
    if remaining > 0:
        time.sleep(remaining)


# ── 缓出缓入速度曲线 ──


def _ease_curve(t):
    """三段速度曲线: 加速 → 匀速 → 减速"""
    if t < 0.25:
        s = t / 0.25
        return 0.2 * s * s
    elif t < 0.75:
        return 0.2 + (t - 0.25) / 0.5 * 0.6
    else:
        s = (t - 0.75) / 0.25
        return 0.8 + 0.2 * (1 - (1 - s) ** 2)


class HumanMouse:
    """人类化鼠标控制器

    - move_to: SetCursorPos 沿贝塞尔路径平滑移动（可靠、精确、肉眼可见）
    - click: 移动到位 → 明显停顿 → 按下 → 保持 → 释放
    - 默认速度较慢，便于玩家观察每个步骤
    """

    STEP_INTERVAL = 0.015  # 15ms，比 10ms 稍慢，移动更顺滑可见

    def __init__(self, speed="slow", jitter=10, click_delay=(150, 400),
                 move_pause=(0.2, 0.5), debug=False, click_method="sendinput"):
        """
        Args:
            speed: "very_slow" / "slow" / "medium" / "fast"
                very_slow: 明显可见，适合调试和首次验证
                slow: 默认，平衡安全与速度
            jitter: 鼠标移动随机抖动幅度(px)
            click_delay: 点击后随机延迟范围(ms)
            move_pause: 移动到位后、点击前的停顿(秒)，让玩家看到光标到位
            debug: 是否输出坐标/路径调试信息
            click_method: 点击方案
                "sendinput":   SendInput API（默认，微软官方推荐）
                "mouse_event": 旧版 mouse_event API
                "pydirectinput": pydirectinput 库（需 pip install）
                "postmessage":  PostMessage 向窗口发消息
        """
        self.speed = speed
        self.jitter = jitter
        self.click_delay = click_delay
        self.move_pause = move_pause
        self.debug = debug
        self.click_method = click_method
        self._stopped = False

        # pydirectinput 可用性检查
        if click_method == "pydirectinput" and not _HAS_PDI:
            if debug:
                print("[Mouse] pydirectinput 未安装，降级为 sendinput")
            self.click_method = "sendinput"

        # postmessage 方案需要前台窗口句柄
        self._target_hwnd = None

        # 单位：秒。数值越大，移动越慢、越明显
        speed_map = {
            "very_slow": (1.2, 2.5),
            "slow": (0.6, 1.2),
            "medium": (0.3, 0.7),
            "fast": (0.15, 0.35),
        }
        self._move_duration = speed_map.get(speed, (0.6, 1.2))

    def stop(self):
        """紧急停止"""
        self._stopped = True

    def reset(self):
        self._stopped = False

    @property
    def stopped(self):
        return self._stopped

    # ═══════════════ 高斯分布点击随机化 ═══════════════

    @staticmethod
    def _gaussian_point(cx, cy, w, h):
        """在目标矩形内按高斯分布采样一点

        标准差 = 尺寸/3，最多 8 次拒绝采样，失败回退中心。
        """
        if w <= 2 or h <= 2:
            return cx, cy

        std_x = w / 3.0
        std_y = h / 3.0

        for _ in range(8):
            dx = int(round(random.gauss(0, std_x)))
            dy = int(round(random.gauss(0, std_y)))
            if abs(dx) <= w // 2 and abs(dy) <= h // 2:
                return cx + dx, cy + dy

        return cx, cy

    # ═══════════════ 贝塞尔曲线路径生成 ═══════════════

    @staticmethod
    def _bezier_path(x0, y0, x1, y1, jitter, steps=None):
        """三次贝塞尔曲线 + 缓出缓入速度曲线，生成自然移动路径

        Args:
            steps: 固定步数；为 None 时根据速度设置自动计算
        """
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist < 2:
            return [(x1, y1)]

        # 控制点偏移
        offset = min(dist * 0.12, 50) * random.choice([-1, 1])
        dx, dy = x1 - x0, y1 - y0
        perp_x = -dy / dist * offset
        perp_y = dx / dist * offset

        cp1 = (x0 + (x1 - x0) * 0.3 + perp_x * 0.5,
               y0 + (y1 - y0) * 0.3 + perp_y * 0.5)
        cp2 = (x0 + (x1 - x0) * 0.7 + perp_x,
               y0 + (y1 - y0) * 0.7 + perp_y)

        if steps is None:
            # 固定 15ms 一步，根据距离和时长计算步数
            duration = random.uniform(0.15, 0.4)
            steps = max(20, int(duration / HumanMouse.STEP_INTERVAL))
        jitter_scale = jitter * 0.3

        points = []
        for i in range(1, steps + 1):
            t = i / steps
            et = _ease_curve(t)

            mt = 1 - et
            px = mt**3 * x0 + 3*mt**2*et*cp1[0] + 3*mt*et**2*cp2[0] + et**3 * x1
            py = mt**3 * y0 + 3*mt**2*et*cp1[1] + 3*mt*et**2*cp2[1] + et**3 * y1

            if i < steps:
                px += random.uniform(-jitter_scale, jitter_scale)
                py += random.uniform(-jitter_scale, jitter_scale)
            points.append((px, py))

        points[-1] = (float(x1), float(y1))
        return points

    # ═══════════════ 核心方法 ═══════════════

    def move_to(self, x, y):
        """平滑移动鼠标到目标坐标

        使用 SetCursorPos 沿贝塞尔路径逐步移动。默认速度较慢，
        玩家可以清楚看到光标从当前位置移到目标位置。
        """
        if self._stopped:
            return

        x0, y0 = _get_cursor_pos()

        # 目标加高斯随机偏移（不总是精确到点）
        tx, ty = self._gaussian_point(int(x), int(y), 6, 6)

        if self.debug:
            print(f"[Mouse] move_to from=({x0},{y0}) target=({x},{y}) rand_target=({tx},{ty})")

        dist = math.hypot(tx - x0, ty - y0)
        if dist < 2:
            _set_cursor_pos(tx, ty)
            return

        # 根据速度生成路径步数
        duration = random.uniform(*self._move_duration)
        steps = max(25, int(duration / self.STEP_INTERVAL))
        path = self._bezier_path(x0, y0, tx, ty, self.jitter, steps=steps)

        next_time = time.perf_counter()
        for px, py in path:
            if self._stopped:
                return
            _set_cursor_pos(px, py)
            next_time += self.STEP_INTERVAL
            _sleep_until(next_time)

        # 最终精确定位
        _set_cursor_pos(tx, ty)

        if self.debug:
            cx, cy = _get_cursor_pos()
            print(f"[Mouse] move_to finished current=({cx},{cy}) target=({tx},{ty}) err=({cx-tx},{cy-ty})")

    def click(self, x=None, y=None):
        """人类化点击: 移动到目标 → 明显停顿 → 按下 → 保持 → 释放

        Args:
            x, y: 目标屏幕坐标。为 None 时只在当前位置点击。
        """
        if self._stopped:
            return

        if x is not None and y is not None:
            self.move_to(x, y)
            if self._stopped:
                return

            # 移动到位后，再精确定位一次，并停顿一下让玩家/游戏看到光标到位
            tx, ty = self._gaussian_point(int(x), int(y), 4, 4)
            _set_cursor_pos(tx, ty)

            pause = random.uniform(*self.move_pause)
            time.sleep(pause)

            if self.debug:
                cx, cy = _get_cursor_pos()
                print(f"[Mouse] click current=({cx},{cy}) target=({tx},{ty}) "
                      f"err=({cx-tx},{cy-ty}) method={self.click_method}")

        # 根据方案发送点击事件
        self._do_click(tx if x is not None else None,
                       ty if y is not None else None)

        # 点击后随机延迟
        delay = random.uniform(*self.click_delay) / 1000.0
        time.sleep(delay)

    def _do_click(self, x=None, y=None):
        """根据 click_method 发送鼠标按下/释放事件"""
        method = self.click_method

        if method == "sendinput":
            _sendinput_click(down=True)
            time.sleep(random.uniform(0.08, 0.18))
            _sendinput_click(down=False)

        elif method == "mouse_event":
            _mouse_event_click(down=True)
            time.sleep(random.uniform(0.08, 0.18))
            _mouse_event_click(down=False)

        elif method == "pydirectinput":
            _pydirectinput_click(down=True)
            time.sleep(random.uniform(0.08, 0.18))
            _pydirectinput_click(down=False)

        elif method == "postmessage":
            # 获取前台窗口作为目标（用户应先将游戏窗口置于前台）
            hwnd = self._target_hwnd or _get_foreground_window()

            # 校验窗口有效性：窗口句柄非空、窗口存在、且仍是前台窗口
            valid = False
            if hwnd:
                try:
                    is_window = ctypes.windll.user32.IsWindow(hwnd)
                    foreground = _get_foreground_window()
                    valid = bool(is_window) and hwnd == foreground
                except Exception:
                    valid = False

            if valid:
                click_x = int(x) if x is not None else 0
                click_y = int(y) if y is not None else 0
                _postmessage_click(hwnd, click_x, click_y, down=True)
                time.sleep(random.uniform(0.08, 0.18))
                _postmessage_click(hwnd, click_x, click_y, down=False)
            else:
                reason = "未找到目标窗口" if not hwnd else "目标窗口已失去焦点"
                print(f"[Mouse] postmessage: {reason}，降级为 sendinput")
                _sendinput_click(down=True)
                time.sleep(random.uniform(0.08, 0.18))
                _sendinput_click(down=False)
        else:
            _sendinput_click(down=True)
            time.sleep(random.uniform(0.08, 0.18))
            _sendinput_click(down=False)

        if self.debug:
            print(f"[Mouse] click event sent via {method}")

    def drag_draw(self, points):
        """按住左键滑动绘制：移动到第一个点 → 按下左键 → 依次滑过各点 → 释放

        适合同色、同行、相邻的连续格子，比逐格点击快得多。

        Args:
            points: [(x1, y1), (x2, y2), ...] 至少 2 个点
        """
        if self._stopped or len(points) < 2:
            return

        # 移动到第一个点，停顿让游戏感知
        self.move_to(points[0][0], points[0][1])
        if self._stopped:
            return
        time.sleep(random.uniform(*self.move_pause))

        # 按下左键
        self._do_click_down()

        # 依次滑到后续各点，每个点稍作停留确保游戏捕获
        for i in range(1, len(points)):
            if self._stopped:
                self._do_click_up()
                return
            self.move_to(points[i][0], points[i][1])
            time.sleep(random.uniform(0.08, 0.15))

        # 释放左键
        self._do_click_up()
        delay = random.uniform(*self.click_delay) / 1000.0
        time.sleep(delay)

    def _do_click_down(self):
        """仅按下左键（不释放），供 drag_draw 使用"""
        method = self.click_method
        if method == "sendinput":
            _sendinput_click(down=True)
        elif method == "mouse_event":
            _mouse_event_click(down=True)
        elif method == "pydirectinput":
            _pydirectinput_click(down=True)
        elif method == "postmessage":
            hwnd = self._target_hwnd or _get_foreground_window()
            valid = hwnd and ctypes.windll.user32.IsWindow(hwnd) and hwnd == _get_foreground_window()
            if valid:
                _postmessage_click(hwnd, 0, 0, down=True)
            else:
                _sendinput_click(down=True)
        else:
            _sendinput_click(down=True)

    def _do_click_up(self):
        """仅释放左键，供 drag_draw 使用"""
        method = self.click_method
        if method == "sendinput":
            _sendinput_click(down=False)
        elif method == "mouse_event":
            _mouse_event_click(down=False)
        elif method == "pydirectinput":
            _pydirectinput_click(down=False)
        elif method == "postmessage":
            hwnd = self._target_hwnd or _get_foreground_window()
            valid = hwnd and ctypes.windll.user32.IsWindow(hwnd) and hwnd == _get_foreground_window()
            if valid:
                _postmessage_click(hwnd, 0, 0, down=False)
            else:
                _sendinput_click(down=False)
        else:
            _sendinput_click(down=False)

    def drag_scroll(self, anchor_x, anchor_y, dist_px, down=True):
        """按住左键拖拽滚动染料板（游戏不响应滚轮）

        Args:
            anchor_x, anchor_y: 起始坐标（通常为染料板中心）
            dist_px: 拖拽距离（像素），例如 2 行高度
            down: True=向上拖（下面内容拉上来），False=向下拖（滚回顶部）
        """
        if self._stopped:
            return

        self.move_to(anchor_x, anchor_y)
        if self._stopped:
            return
        time.sleep(random.uniform(*self.move_pause))

        # 按下左键
        self._do_click_down()
        time.sleep(random.uniform(0.05, 0.1))

        # 缓慢拖拽，带缓出曲线
        steps = max(15, int(abs(dist_px) / 5))
        sign = -1 if down else 1
        target_y = anchor_y + sign * dist_px

        for i in range(1, steps + 1):
            if self._stopped:
                self._do_click_up()
                return
            t = i / steps
            et = 1 - (1 - t) ** 2  # ease-out：开始快、结尾慢
            cur_y = int(anchor_y + sign * dist_px * et)
            _set_cursor_pos(int(anchor_x), cur_y)
            time.sleep(0.02)

        _set_cursor_pos(int(anchor_x), target_y)
        time.sleep(random.uniform(0.05, 0.1))

        # 释放左键
        self._do_click_up()
        time.sleep(random.uniform(0.3, 0.5))

    def scroll(self, delta):
        """滚轮滚动，delta 为正向上、为负向下"""
        if self._stopped:
            return
        if self.click_method == "sendinput":
            _sendinput_wheel(delta)
        elif self.click_method == "pydirectinput" and _HAS_PDI:
            _pydirectinput_wheel(delta)
        else:
            wheel_amount = int(delta * WHEEL_DELTA)
            ctypes.windll.user32.mouse_event(
                MOUSEEVENTF_WHEEL, 0, 0, wheel_amount, 0)
        time.sleep(random.uniform(0.4, 0.8))

    def random_pause(self, min_s=0.2, max_s=0.5):
        """随机停顿"""
        time.sleep(random.uniform(min_s, max_s))
