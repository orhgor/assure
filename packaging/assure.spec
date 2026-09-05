# PyInstaller spec for the Assure desktop app (macOS .app, Windows .exe, Linux binary).
# Build: scripts/build-desktop.sh   or  scripts/build-desktop.ps1
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).resolve().parent
PKG = ROOT / "prompt_matrix"

datas = [
    (str(PKG / "config.json"), "prompt_matrix"),
    (str(PKG / "static"), "prompt_matrix/static"),
    (str(PKG / "templates"), "prompt_matrix/templates"),
    (str(PKG / "mcp.example.json"), "prompt_matrix"),
]
if (PKG / "translations").is_dir():
    datas.append((str(PKG / "translations"), "prompt_matrix/translations"))
if (PKG / "examples").is_dir():
    datas.append((str(PKG / "examples"), "prompt_matrix/examples"))

hiddenimports = [
    "flask",
    "flask_httpauth",
    "flask_babel",
    "jinja2",
    "litellm",
    "tiktoken",
    "instructor",
    "pydantic",
    "dotenv",
    "rich",
    "prompt_matrix.web",
    "prompt_matrix.cli",
    "prompt_matrix.desktop",
]

for name in ("litellm", "tiktoken", "flask_babel"):
    try:
        d, _bins, hints = collect_all(name)
        datas += d
        hiddenimports += hints
    except Exception:
        pass
try:
    datas += collect_data_files("prompt_matrix")
except Exception:
    pass

a = Analysis(
    [str(PKG / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["prompt_matrix.swarm"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Assure",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Assure",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Assure.app",
        icon=None,
        bundle_identifier="com.getassure.workbench",
        info_plist={
            "CFBundleName": "Assure",
            "CFBundleDisplayName": "Assure",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
        },
    )
