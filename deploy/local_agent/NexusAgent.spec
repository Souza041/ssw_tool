# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
browser_root = os.path.join(root, "build", "playwright-browsers")
playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")
datas = list(playwright_datas)
if os.path.isdir(browser_root):
    datas.append((browser_root, "playwright-browsers"))

a = Analysis(
    [os.path.join(root, "scripts", "nexus_agent_entry.py")],
    pathex=[root],
    binaries=playwright_binaries,
    datas=datas,
    hiddenimports=playwright_hidden + ["pystray._win32", "PIL.Image", "PIL.ImageDraw"],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NexusAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
)
