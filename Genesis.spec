# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
import sys
from pathlib import Path

__version__ = '1.0.0'

datas = []
binaries = []
hiddenimports = ['webview.platforms.winforms']

# Collect all pywebview assets (DLLs, .js, etc.)
tmp_ret = collect_all('pywebview')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# Collect Edge WebView2 runtime (msedgewebview2.dll)
try:
    import webview.platforms.winforms as wf
    edge_dir = Path(wf.__file__).parent / 'edge_engine'
    if edge_dir.is_dir():
        for f in edge_dir.rglob('*'):
            if f.is_file():
                rel = f.relative_to(edge_dir.parent)
                datas.append((str(f), str(rel.parent)))
except Exception:
    pass

# Project-specific data files
# PyInstaller provides SPECPATH (always available during exec of the spec)
ROOT = Path(SPECPATH) if 'SPECPATH' in dir() else Path('.').resolve()

datas += [
    (str(ROOT / 'gui' / 'profile_picker.html'), 'gui'),
    (str(ROOT / 'genesis_icon.ico'), '.'),
    (str(ROOT / '.env.example'), '.'),
]

a = Analysis(
    ['launch_gui.py'],
    pathex=[],
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
    name='Genesis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ROOT / 'genesis_icon.ico')],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Genesis',
)
