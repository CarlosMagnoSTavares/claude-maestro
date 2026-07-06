# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('C:\\Users\\carlo\\Desktop\\CMST\\CLAUDE_CODE_MAESTRO\\maestro\\static', 'maestro\\static'), ('C:\\Users\\carlo\\Desktop\\CMST\\CLAUDE_CODE_MAESTRO\\maestro\\wa', 'maestro\\wa')]
binaries = []
hiddenimports = ['clr', 'multipart', 'PIL.Image']
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('qrcode')
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:\\Users\\carlo\\Desktop\\CMST\\CLAUDE_CODE_MAESTRO\\installer\\launcher.py'],
    pathex=['C:\\Users\\carlo\\Desktop\\CMST\\CLAUDE_CODE_MAESTRO'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClaudeMaestro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\carlo\\Desktop\\CMST\\CLAUDE_CODE_MAESTRO\\installer\\maestro.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ClaudeMaestro',
)
