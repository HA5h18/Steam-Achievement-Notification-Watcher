# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['steam_watcher.py'],
    pathex=[],
    binaries=[],
    datas=[('unlock.mp3', '.')],
    hiddenimports=[],
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
    name='steam_watcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='steam_watcher',
)

app = BUNDLE(
    coll,
    name='Steam Watcher.app',
    icon='source.icns',
    bundle_identifier='com.HAsh18.steamwatcher',
)