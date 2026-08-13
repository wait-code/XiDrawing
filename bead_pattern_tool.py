#!/usr/bin/env python3


import sys, os, traceback
sys.dont_write_bytecode = True
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

def main():
    from bead_pattern_tool.gui import App
    App().mainloop()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        # 兜底：写入日志文件
        log = os.path.join(os.path.dirname(__file__), "_crash.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write(err)
        # 尝试用 tkinter 弹窗显示错误
        try:
            import tkinter.messagebox
            root = __import__('tkinter').Tk()
            root.withdraw()
            tkinter.messagebox.showerror("程序崩溃", f"发生未捕获异常，已写入 _crash.log\n\n{err[-1500:]}")
            root.destroy()
        except Exception:
            pass
        print(err, file=sys.stderr)
        sys.exit(1)
