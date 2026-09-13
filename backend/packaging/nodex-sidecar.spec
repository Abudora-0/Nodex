# PyInstaller spec for the engine the desktop app launches as a sidecar.
# Build from backend/:  python -m PyInstaller packaging/nodex-sidecar.spec --noconfirm

import os

from PyInstaller.utils.hooks import collect_submodules

backend = os.path.abspath(os.path.join(SPECPATH, ".."))

hidden = (
    collect_submodules("nodex")
    + collect_submodules("uvicorn")
    + collect_submodules("cryptography.hazmat.primitives")
    + ["cryptography.hazmat.backends.openssl"]
)

a = Analysis(
    [os.path.join(SPECPATH, "sidecar_entry.py")],
    pathex=[backend],
    hiddenimports=hidden,
    excludes=["tkinter", "_tkinter", "pytest", "httpx"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# console=True keeps stdout valid for the NODEX_READY handshake; the desktop
# shell spawns it with CREATE_NO_WINDOW, so no console window appears.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="nodex-sidecar",
    console=True,
    upx=False,
    runtime_tmpdir=None,
)
