
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all
import os

# Project directory
project_dir = os.path.abspath(".")

# Application resources
datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    
]

binaries = []
hiddenimports = []

# Required packages
packages = [
    'webview',
    'flask',
    'flask_login',
    'flask_wtf',
    'flask_sqlalchemy',
    'sqlalchemy',
    'werkzeug',
    'jinja2',
    'wtforms',
    'cryptography',
]

# Collect package dependencies and resources
for package in packages:
    tmp_ret = collect_all(package)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

# Include Python encoding modules
hiddenimports += [
    'encodings',
    'encodings.idna',
    'encodings.utf_8',
    'encodings.ascii',
]

# Include additional application modules
hiddenimports += [
    'licensing',
    'sqlite3',
    'webview.platforms.edgechromium',
]

# Remove duplicate hidden imports
hiddenimports = list(set(hiddenimports))

# PyInstaller analysis
a = Analysis(
    ['app.py'],
    pathex=[project_dir],
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

# Python archive
pyz = PYZ(a.pure)

# Application executable
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MobileStoreERP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Collect final application
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MobileStoreERP',
)
