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

## 重要提醒

- `auto_draw/` 是 AI 编写的废稿，不好用、不稳定，仅存档参考，不建议使用。
- 拼豆算法优化参考并致谢 [TosakaWolf/box-share](https://github.com/TosakaWolf/box-share/blob/main/bead_engine_optimized.py) 提供的 `bead_engine_optimized.py`。
- 作者精力有限，可能无法持续维护，深表歉意。欢迎 fork 与 PR。

## License

[Apache License 2.0](LICENSE)
