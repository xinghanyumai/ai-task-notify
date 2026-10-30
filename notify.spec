# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置 —— `通知.exe` 的唯一构建入口。

用法（不要直接 `pyinstaller notify.spec`，用 `tools/build_exe.py`，
它会在打包前后各做一遍检查）：

    python tools/build_exe.py            # onefile，默认
    python tools/build_exe.py --onedir   # onedir，只用来对比启动耗时

产物：项目根目录的 `通知.exe`（`build_exe.py` 用 `--distpath` 指过去；
单独跑 `pyinstaller notify.spec` 不带 `--distpath` 的话仍会落到 `dist/`）。

--------------------------------------------------------------------------
三条不能改错的约定
--------------------------------------------------------------------------
1. **`datas` 里 `('webui', 'webui')` 是整目录拷贝。** 少一个 `.css` / `.js`
   的后果是：源码形态完全正常（走的是仓库里的真实目录），exe 里白屏或没样式。
   本机测不出来，用户装上才发现。核对方法见 `tools/build_exe.py` 的
   `check_webui()`：源码文件数必须等于 exe 内文件数。

2. **`excludes` 只排「运行程序时一个字节都不需要」的东西。**
   排掉开发/验收工具链（cv2 / windows_capture / playwright）能省 80MB+，
   但排错一个在用的模块，症状是「exe 能启动、某一页点开就崩」。
   所以排完必须启动 exe 走一遍五页 + 发一条测试消息。

3. **`console=False` + `manifest` + `version`。**
   前两个决定「双击不弹黑框」和「250% 缩放不糊」，第三个决定
   文件属性里有没有版本号。三个都是用户直接看得见的，不能省。
"""

import os

from PyInstaller.utils.hooks import collect_data_files

# onefile 与 onedir 的区别只在最后两段（EXE 带不带 binaries/datas + 有没有
# COLLECT）。用环境变量切换而不是写两份 spec：两份 spec 一定会漂移。
ONEDIR = os.environ.get("WBN_ONEDIR") == "1"

# 调试版：`WBN_CONSOLE=1` 出一个带控制台的 exe（名字带 -debug，不覆盖交付物）。
# 存在的理由很实在——`console=False` 的 exe 里 `print` 和 traceback 全进黑洞，
# 「用户那边打不开」就只能靠猜。有这个开关，让用户跑一次 debug 版、
# 把窗口里的报错截图发过来，比问十个问题都快。
CONSOLE = os.environ.get("WBN_CONSOLE") == "1"
NAME = "通知-debug" if CONSOLE else "通知"

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("webui", "webui"),                     # 前端资源：整目录，见上面第 1 条
        ("assets/icon.ico", "assets"),          # 窗口/托盘图标
        # 首次运行的配置模板。运行期其实用不到（config.DEFAULT_CONFIG 在代码里），
        # 但带上它，「解压出来看看默认长什么样」这件事才有答案，代价 1KB。
        ("config.example.json", "."),
    ]
    # ---- pywebview 的 JS 注入文件：**漏了界面就是一块死页面** ----
    #
    # `pywebview/js/`（api.js / state.js / customize.js / finish.js / lib/*.js）
    # 是 pywebview 在导航完成后**从磁盘读出来、注入到页面里**的。
    # `_pyinstaller_hooks_contrib` 的 hook-webview 只收了 `webview/lib`
    # （.dll），**没收 `webview/js`**——这是上游 hook 的缺口。
    #
    # 漏了之后的症状极难自查：窗口正常打开、标题栏正常、CSS 正常、
    # 骨架屏都在，**只有 `window.pywebview` 不存在**，于是前端所有 bridge 调用
    # 永远不返回，页面停在「正在读取…」。Python 侧一行异常都没有
    # （`get_js_dir()` 抛的 FileNotFoundError 被 pywebview 的 logger 吞了），
    # 只有 app.py 的 10 秒看门狗会报「no_render」——指向的还是 WebView2。
    + collect_data_files("webview", subdir="js"),
    hiddenimports=[
        # ---- 五个子命令模块：**这一组漏了，exe 照样能启动，只是子命令全废** ----
        # app.py 的 _delegate() 是 `importlib.import_module(cmd)`，cmd 是个变量。
        # PyInstaller 只做静态分析，看到这种写法就当没 import——于是
        # `通知.exe hook` 会在干净机器上 ModuleNotFoundError，
        # 而这台机器上 `python app.py hook` 完全正常。
        #
        # 后果按严重度排：
        #   hook     → 宿主事件一个都收不到（整个产品的核心功能没了）
        #   dispatch → hook 起来了但发不出消息
        #   install  → 装不上
        #   tray     → 没托盘（最轻）
        #   selftest → 自检跑不了
        # 这不是理论风险：第一版 spec 就漏了这五个，exe 12.1MB、`--version`
        # 退出码 0、webui 11==11，全绿，但 tray/hook 一条都用不了。
        "hook",
        "dispatch",
        "install",
        "selftest",
        "tray",
        # pywebview 在 Windows 上的后端是 winforms.py（内部走 pythonnet → WebView2）。
        # guilib.py 里是 `import webview.platforms.winforms as guilib` 写在函数里，
        # 静态分析能扫到，但扫不到的条件分支一旦改版就静默丢后端——显式钉住。
        "webview.platforms.winforms",
        # 6.x 之前的 pywebview 走这个文件；留着不影响体积（几 KB），
        # 但能挡住「升级/降级 pywebview 后 exe 打不开」。
        "webview.platforms.edgechromium",
        # pythonnet 的 `import clr` 被 hook-clr 处理，这里补它内部 import 的两个
        # 模块，避免 clr 初始化时才 ImportError（那时已经开不出窗口了）。
        "clr",
        # 注：任务书 §3 列的 `PIL._tkinter_finder` **故意不加**。
        # 它模块顶层就 `import tkinter`，而下面 excludes 里排了 tkinter——
        # 加进来只会得到一个坏掉的模块 + 一条 hidden import 警告。
        # 它的作用是给 Pillow 的 ImageTk 找 Tcl/Tk 库，本项目不用 ImageTk。
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # ---- 从未使用 ----
        "tkinter",            # W5 已下线 tkinter 版 UI；降级提示走 MessageBoxW
        "unittest", "pydoc", "doctest", "test",
        "numpy", "pandas", "matplotlib", "scipy",
        # ---- 开发 / 验收工具链：运行程序一个字都不用，排掉省 80MB+ ----
        # cv2 / windows_capture → 只被 tools/shot_webview.py import（截图用）。
        #   WebView2 是硬件合成，ImageGrab / PrintWindow 只拍到黑块，
        #   所以验收截图必须走 WGC，而 WGC 依赖这俩。tools/ 不打包。
        "cv2",
        "windows_capture",
        # playwright → 只被 tools/a11y_contrast.py import（扫对比度），
        #   它还要真实窗口 + CDP，CI 上跑不了，只能人工验收时用。
        "playwright",
        # ---- 其它平台的 GUI 后端：Windows 上永远不会 import ----
        "webview.platforms.gtk",
        "webview.platforms.qt",
        "webview.platforms.cocoa",
        "webview.platforms.android",
        "webview.platforms.cef",
        "webview.platforms.mshtml",   # IE 内核，Win10/11 上已被 WebView2 取代
        "PyQt5", "PyQt6", "PySide2", "PySide6",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

_common = dict(
    name=NAME,
    icon="assets/icon.ico",
    # 双击不弹控制台黑框。副作用：双击时 `sys.stdout` 是 None，`print()`
    # 全部丢弃。但**别由此推出「重定向也读不到」**——重定向时 Python 会建一个
    # 真的 stdout（编码 = 系统 ANSI 代码页），内容写得出来，只是中文在英文
    # Windows（cp1252）上会抛 UnicodeEncodeError。2026-10-30 CI 的冒烟就是
    # 卡在这上面，修在 app.py 的 `_harden_stdio`。
    # 要排查问题就用 `WBN_CONSOLE=1` 出调试版（见上面第 39 行）。
    console=CONSOLE,
    version="version_info.txt",
    # UPX 会把 exe 压得更小，但「自解压 + 改过节的二进制」是杀软误报的重灾区。
    # 这个项目的分发方式是「发给同事一个 exe」，被 Windows Defender 拦下来
    # 比多 8MB 严重得多。
    upx=False,
    manifest="notify.manifest",
)

if ONEDIR:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **_common,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="通知",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        # runtime_tmpdir=None = 解压到系统临时目录（%TEMP%\_MEIxxxxxx），退出即删。
        # 换成 "." 能复用解压结果、二次启动快，但 exe 旁边会多出一个 `_MEIxxxx`，
        # 用户删不掉也看不懂，和「绿色单文件」的承诺冲突。
        runtime_tmpdir=None,
        **_common,
    )
