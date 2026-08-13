"""字体缓存，多字号复用"""
from PIL import ImageFont

_CACHE = {}
def get_cn_font(size=12, bold=False):
    key = (size, bold)
    if key in _CACHE:
        return _CACHE[key]
    if bold:
        paths = [
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/msjh.ttc",
        ]
    else:
        paths = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/msjh.ttc",
        ]
    for p in paths:
        try:
            f = ImageFont.truetype(p, size)
            _CACHE[key] = f
            return f
        except Exception:
            pass
    f = ImageFont.load_default()
    _CACHE[key] = f
    return f
