# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置。在项目根目录运行:  pyinstaller --noconfirm packaging/cs2musicbox.spec
# 产物:dist/CS2MusicBox/CS2MusicBox.exe(加旁边的 _internal 文件夹,整个文件夹一起分发)
#
# 选 onedir(文件夹)而不是 onefile(单个 exe):
#   librosa/numba/scipy 加起来很大,onefile 每次启动都要先解压到临时目录,慢好几秒;
#   onedir 启动快,也更不容易被杀毒软件误报。
import os
import shutil
import sys

ROOT = os.path.dirname(SPECPATH)                      # 项目根目录(这个文件在 packaging/ 里)
IS_WIN = sys.platform == "win32"

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "web"), "web")],       # 网页界面(只读资源)
    hiddenimports=[],
    excludes=[
        # 用不到的大件:避免构建机上碰巧装了它们时被打进去
        "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pandas", "PyQt5", "PyQt6", "PySide2",
        "PySide6", "torch", "torchaudio", "transformers", "tensorflow", "pytest",
        # 社区服务器的依赖(本地程序只用标准库访问社区)
        "fastapi", "uvicorn", "starlette", "pydantic", "psycopg", "psycopg_pool",
    ],
    # 注:librosa 用 numba 的 cache=True 缓存编译结果。打包后 numba 会自动改用用户缓存目录
    # (Windows 上是 %LOCALAPPDATA%\numba),第一次分析稍慢,之后都走缓存,不需要额外处理。
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],                     # UTF-8 模式:输出被重定向到文件时中文日志也不会出错
    exclude_binaries=True,
    name="CS2MusicBox",
    console=True,                                     # 保留控制台窗口:能看到日志,关窗口即退出
    icon=os.path.join(SPECPATH, "icon.ico") if IS_WIN else None,
    version=os.path.join(SPECPATH, "version_info.txt") if IS_WIN else None,
    upx=False,                                        # 不压缩:UPX 压过的 DLL 更容易被杀毒软件误报
    debug=False,
    strip=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="CS2MusicBox",
    upx=False,
    strip=False,
)

# 给用户看的使用说明和图文教程放在 exe 旁边
shutil.copy(os.path.join(SPECPATH, "使用说明.txt"), os.path.join(DISTPATH, "CS2MusicBox", "使用说明.txt"))
sys.path.insert(0, SPECPATH)
try:
    import build_tutorial
    build_tutorial.build(os.path.join(DISTPATH, "CS2MusicBox", "新手教程.html"))
except ImportError as e:                              # 没装 markdown 库时跳过,不影响程序本身
    print("WARNING: 没有生成 新手教程.html:", e)
