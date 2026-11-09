# -*- mode: python ; coding: utf-8 -*-
# Build on Windows:  pyinstaller --clean IPV4.spec   ->  dist\IPV4Switcher.exe

a = Analysis(
    ['IPV4.py'],
    pathex=[],
    binaries=[],
    datas=[],
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
    a.binaries,
    a.datas,
    [],
    name='IPV4Switcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX-packed exes often trip antivirus false positives
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,          # netsh needs admin; prompt for UAC at launch
    version='version_info.txt',
    # icon='ipv4.ico',       # drop an .ico in this folder and uncomment
)
