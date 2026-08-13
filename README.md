# XiDrawing（夕的画板）

为游戏社区玩家打造的拼豆图纸生成器。上传游戏截图或角色图片，一键生成可照着拼豆的施工图纸（网格图 + 色号标注）。

## 功能特性

- 多种渲染模式：照片模式（线性光面积采样，色彩还原最佳）、插画模式（提取主色，适合二次元/Q 版）、边缘模式（保留硬轮廓，适合立绘）、抖动模式（蛇形 Floyd-Steinberg 误差扩散，适合渐变/大幅面）
- 拼豆算法核心（`core/`）：基于 `BeadEngineOptimized`（OKLab 色差 / 加权 KMeans / Floyd-Steinberg），选色严格限制在真实 40 色拼豆色板内
- KMeans 精准模式、主色法转换、Q 版/立绘预设等多种算法
- 自动裁剪、背景检测、平滑边缘、轮廓描边
- 输出网格 12~256 可调（默认 24×24），支持导出图纸

## 项目结构

```
XiDrawing/
├── bead_pattern_tool.py        # 入口脚本
├── requirements.txt
├── icon.ico
├── scripts/
│   └── benchmark.py            # 性能基准脚本
└── bead_pattern_tool/
    ├── config.py               # 全局配置
    ├── core/                   # 拼豆算法核心
    │   ├── bead_engine.py      # BeadEngineOptimized 主引擎
    │   ├── bead_render_engine.py  # 照片/插画/边缘/抖动渲染
    │   ├── kmeans_beadify.py   # KMeans 精准模式
    │   ├── palette.py          # 40 色拼豆色板
    │   ├── bead_converter.py   # 图片转换/裁剪/预处理
    │   ├── pixels.py           # 像素处理/背景检测/平滑
    │   ├── presets.py          # Q 版/立绘预设
    │   └── render.py           # 图纸渲染
    ├── gui/                    # tkinter 界面
    └── auto_draw/              # 自动绘图（废稿，见下方提醒）
```

## 安装与使用

```bash
pip install -r requirements.txt
python bead_pattern_tool.py
```

## 依赖说明

核心依赖（必装）：`numpy`、`opencv-python`、`Pillow`、`scikit-learn`、`scipy`

可选依赖：
- `pydirectinput`：仅 `auto_draw/`（废稿）使用，核心功能不需要

headless 提示：在无 GUI 的服务器/容器上运行核心算法时，可把 `opencv-python` 替换为 `opencv-python-headless`（两者互斥，不可同时安装）。人脸检测（face crop）与 `auto_draw/` 依赖窗口/摄像头的能力在 headless 环境不可用。注意：`core/` 的调色板与渲染路径会强制 `import cv2`，请务必保证已安装其中之一，否则会直接 `ImportError`。`scipy` 用于固定色板批量最近邻映射（cKDTree）；未安装时自动回退到 numpy 实现，功能不受影响。

## 快速启动

GUI 启动：

```bash
python bead_pattern_tool.py        # 或 python -m bead_pattern_tool
```

CLI / 脚本调用（核心算法 API）：

```python
from bead_pattern_tool.core import make_pattern

grid, pix = make_pattern("input.png", n=24)  # 返回图纸图片与像素色列表
grid.save("pattern.png")
```

PyInstaller 打包：

```bash
pip install pyinstaller
pyinstaller -F -w -i icon.ico bead_pattern_tool.py
```

## 性能提示

- 大尺寸图片转换会很慢：核心算法包含多次颜色空间转换、空间平滑与误差扩散循环，网格尺寸（rows/cols）与 `max_colors` 越大耗时越高。
- 建议先用 24×24 或 48×48 试跑确认效果，再加大尺寸。
- `dither` 抖动模式的误差扩散为顺序循环，相对更慢；`photo` 模式整体最轻量。
- 固定色板（`--palette` 或 `build_catalog()`）场景已启用快速最近邻映射（`core/fast_palette_map.py`，OKLab 预计算 + cKDTree 批量查询），结果与全矩阵最近邻一致，内存占用更低。优化方案详见 `docs/PERFORMANCE_OPTIMIZATION.md`。
- 耗时主要受输入分辨率、网格尺寸与渲染模式共同影响，可用下方基准脚本实测。

## 性能基准

`scripts/benchmark.py` 可对任意图片运行性能分析，输出每个「网格尺寸 × 渲染模式」组合的耗时表格：

```bash
# 使用你自己的图片
python scripts/benchmark.py --image path/to/your_image.png

# 指定尺寸与模式组合
python scripts/benchmark.py --image path/to/your_image.png --sizes 24 48 96 --modes photo illustration edge dither --repeat 1

# 没有现成图片时，用内置渐变测试图（--sample）快速体验
python scripts/benchmark.py --sample --sizes 24 48
```

## 重要提醒

- `auto_draw/` 是 AI 编写的废稿，不好用、不稳定，仅存档参考，不建议使用。
- 拼豆算法优化参考并致谢 [TosakaWolf/box-share](https://github.com/TosakaWolf/box-share/blob/main/bead_engine_optimized.py) 提供的 `bead_engine_optimized.py`。
- 作者精力有限，可能无法持续维护，深表歉意。欢迎 fork 与 PR。

## License

[Apache License 2.0](LICENSE)
