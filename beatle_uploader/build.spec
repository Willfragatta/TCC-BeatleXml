# PyInstaller spec para Beatle Uploader
# Uso: pyinstaller beatle_uploader/build.spec

block_cipher = None

a = Analysis(
    ['beatle_uploader/main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['beatle_uploader.config', 'beatle_uploader.auth', 'beatle_uploader.monitor', 'beatle_uploader.uploader', 'pystray', 'PIL', 'PIL._tkinter_finder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'scipy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BeatleUploader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Sem console (janela gráfica)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
