# -*- mode: python ; coding: utf-8 -*-
# Prefira tools\build_contador.bat na raiz do projeto.
# Se for usar este spec, rode a partir da raiz:
#   pyinstaller beatle_contador\build.spec
# O script precisa ser main.py ao lado deste arquivo. Nao use beatle_contador/main.py
# aqui: o PyInstaller soma a pasta do spec e procura beatle_contador\beatle_contador\main.py.

import os

raiz = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(SPECPATH, "main.py")],
    pathex=[raiz],
    binaries=[],
    datas=[],
    hiddenimports=[
        "beatle_contador.config",
        "beatle_contador.api",
        "beatle_contador.instancia",
        "app.services.contador_painel",
        "pystray",
        "PIL",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BeatleContador",
    console=False,
)
