"""屏幕信息：分辨率、DPI 缩放、多显示器绝对坐标换算。

背景：本进程已设为 PROCESS_PER_MONITOR_DPI_AWARE（见 capture.py），
因此 GetCursorPos / ClientToScreen / SetCursorPos 与 ImageGrab 均使用
「虚拟屏幕物理像素」坐标；但多显示器时虚拟屏幕原点可能为负
（副屏在主屏左侧/上方），ImageGrab 无参 grab() 返回的图像原点位于
虚拟屏幕左上角，与物理坐标存在偏移。

本模块提供：
- get_screen_info(): 汇总虚拟屏幕原点/尺寸 + 每台显示器分辨率与 DPI 缩放
- physical_to_image() / image_to_physical(): 物理屏幕坐标 ↔ 截图图像坐标
"""
import ctypes
from ctypes import wintypes

# 确保 DPI 感知（与 capture.py 一致，进程级）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    pass

# GetSystemMetrics 常量
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def get_virtual_screen_origin():
    """虚拟屏幕左上角（物理像素）。多显示器副屏在主屏左侧/上方时为负值。"""
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(SM_XVIRTUALSCREEN), \
           user32.GetSystemMetrics(SM_YVIRTUALSCREEN)


def get_virtual_screen_size():
    """虚拟屏幕总尺寸（物理像素）"""
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), \
           user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)


def _get_dpi_for_monitor(hmon):
    """查询单台显示器的 DPI（物理像素/逻辑像素 的 96 倍基准值）"""
    try:
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(
            ctypes.c_void_p(hmon), 0,  # MDT_EFFECTIVE_DPI
            ctypes.byref(dpi_x), ctypes.byref(dpi_y),
        )
        return dpi_x.value, dpi_y.value
    except Exception:
        return 96, 96


def get_monitors():
    """枚举所有显示器。

    Returns:
        list[dict]: 每台显示器
            {handle, left, top, right, bottom, width, height, dpi_scale}
            left/top/right/bottom 为虚拟屏幕物理像素（可为负），
            dpi_scale 为缩放比例（如 1.0 / 1.25 / 1.5）。
    """
    monitors = []

    @ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )
    def _cb(hmon, _hdc, lprc, _lparam):
        r = lprc.contents
        dpi_x, dpi_y = _get_dpi_for_monitor(hmon)
        monitors.append({
            "handle": hmon,
            "left": r.left, "top": r.top,
            "right": r.right, "bottom": r.bottom,
            "width": r.right - r.left,
            "height": r.bottom - r.top,
            "dpi_scale": round(dpi_x / 96.0, 3),
        })
        return 1  # 继续枚举

    ctypes.windll.user32.EnumDisplayMonitors(None, None, _cb, 0)
    return monitors


def get_screen_info():
    """汇总屏幕信息：虚拟屏原点/尺寸 + 每台显示器详情。"""
    vx, vy = get_virtual_screen_origin()
    vw, vh = get_virtual_screen_size()
    return {
        "virtual_left": vx,
        "virtual_top": vy,
        "virtual_width": vw,
        "virtual_height": vh,
        "monitors": get_monitors(),
    }


def physical_to_image(x, y):
    """物理屏幕坐标（ClientToScreen / SetCursorPos / ImageGrab bbox 使用）
    → ImageGrab 无参 grab() 图像坐标。"""
    vx, vy = get_virtual_screen_origin()
    return x - vx, y - vy


def image_to_physical(x, y):
    """ImageGrab 无参 grab() 图像坐标 → 物理屏幕坐标（用于 SetCursorPos 点击）。"""
    vx, vy = get_virtual_screen_origin()
    return x + vx, y + vy


def get_dpi_scale(monitor_index=0):
    """主显示器（或指定索引）的 DPI 缩放，如 1.0 / 1.25 / 1.5 / 2.0。

    用于逻辑像素 ↔ 物理像素换算；auto_draw 内部统一使用物理像素，
    仅在需要以「游戏/系统上报的逻辑分辨率」推算基准几何时才需要本值。
    """
    try:
        monitors = get_monitors()
        if not monitors:
            return 1.0
        idx = max(0, min(monitor_index, len(monitors) - 1))
        return float(monitors[idx]["dpi_scale"])
    except Exception:
        return 1.0


def get_resolutions():
    """返回主显示器物理/逻辑分辨率与缩放。

    Returns:
        dict: {physical: (w, h), logical: (w, h), scale: float}
    """
    scale = get_dpi_scale(0)
    monitors = get_monitors()
    if not monitors:
        return {"physical": (0, 0), "logical": (0, 0), "scale": scale}
    pw, ph = monitors[0]["width"], monitors[0]["height"]
    return {
        "physical": (pw, ph),
        "logical": (int(round(pw / scale)), int(round(ph / scale))),
        "scale": scale,
    }


def logical_to_physical(x, y, scale=None):
    """逻辑像素 → 物理像素（scale 缺省时自动检测主屏缩放）。

    例：200% 缩放下 logical_to_physical(1440, 900) == (2880, 1800)。
    """
    if scale is None:
        scale = get_dpi_scale(0)
    return int(round(x * scale)), int(round(y * scale))


def physical_to_logical(x, y, scale=None):
    """物理像素 → 逻辑像素（scale 缺省时自动检测主屏缩放）。"""
    if scale is None:
        scale = get_dpi_scale(0)
    return int(round(x / scale)), int(round(y / scale))


def get_dpi_profile():
    """汇总 DPI 检测结果：缩放、物理/逻辑分辨率、虚拟屏原点、显示器数量。

    供 auto_draw 前置 DPI 检测使用：默认先执行本函数拿到真实环境，
    再据此计算坐标与模板尺度，实现「先检测、后执行」的全设备适配。
    """
    info = get_screen_info()
    res = get_resolutions()
    return {
        "scale": res["scale"],
        "physical": res["physical"],
        "logical": res["logical"],
        "virtual_left": info["virtual_left"],
        "virtual_top": info["virtual_top"],
        "virtual_width": info["virtual_width"],
        "virtual_height": info["virtual_height"],
        "monitor_count": len(info["monitors"]),
        "monitors": info["monitors"],
    }
