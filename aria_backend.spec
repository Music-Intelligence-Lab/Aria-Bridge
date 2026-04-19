# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

datas = [('config', 'config')]
binaries = []
hiddenimports = [
    'core', 'modes', 'ui',
    'mido', 'mido.backends.rtmidi', 'rtmidi',
    'pythonosc', 'pythonosc.dispatcher', 'pythonosc.osc_server',
]

# aria / ariautils — collect_all silently returns empty if the package isn't
# found by its distribution name, so also collect submodules explicitly.
for _pkg in ('aria', 'ariautils'):
    try:
        tmp_ret = collect_all(_pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception:
        pass
    try:
        hiddenimports += collect_submodules(_pkg)
        datas        += collect_data_files(_pkg)
    except Exception:
        pass
try:
    tmp_ret = collect_all('pystray')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    tmp_ret = collect_all('PIL')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
except Exception:
    pass

import os
_entry = os.path.join('real-time', 'ableton_bridge.py')

a = Analysis(
    [_entry],
    pathex=['real-time'],
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
    a.binaries,
    a.datas,
    [],
    name='aria_backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
