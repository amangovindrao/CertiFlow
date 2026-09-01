# -*- mode: python ; coding: utf-8 -*-
"""
CertiFlow.spec
--------------
PyInstaller recipe for the CertiFlow desktop build.

Build with:

    python -m PyInstaller --noconfirm CertiFlow.spec

Produces a *one-folder* build at ``dist/CertiFlow/``. One folder rather than
one file on purpose:

  * it starts immediately, where a one-file build re-extracts ~100 MB of
    payload into a temp folder on every launch;
  * the pdfium and Tk native libraries load from a stable path;
  * user data (company profile, database, generated PDFs) sits visibly beside
    the .exe instead of inside a temp folder that is wiped on exit.

Everything the user can change - assets, company profile, saved designs,
output, logs - lives *next to* the .exe and is seeded on first run from the
read-only ``_bundled`` payload inside the build. See ``settings.BASE_DIR`` /
``settings.RESOURCE_DIR``.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

import os

APP_NAME = "CertiFlow"
PROJECT = Path(SPECPATH)

# A windowed build shows no tracebacks. Set CERTIFLOW_CONSOLE=1 to produce a
# console build instead, which prints startup errors straight to the terminal:
#
#   $env:CERTIFLOW_CONSOLE=1; python -m PyInstaller --noconfirm CertiFlow.spec
#
# The shipped build must always be windowed.
CONSOLE = os.environ.get("CERTIFLOW_CONSOLE", "") not in ("", "0", "false")

# --------------------------------------------------------------------------- #
# Read-only payload, seeded next to the .exe on first run
# --------------------------------------------------------------------------- #
# Mapped under "_bundled/..." so settings._seed_from_bundle() can copy it out
# without ever clobbering a file the user has already customised.
bundled = []

for font in sorted((PROJECT / "assets" / "fonts").glob("*.ttf")):
    bundled.append((str(font), "_bundled/assets/fonts"))

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

for kind in ("logo", "signature", "stamp", "watermark"):
    folder = PROJECT / "assets" / kind
    if folder.is_dir():
        for image in sorted(folder.iterdir()):
            # Match on the extension: ".gitkeep" has an empty suffix, so
            # filtering by suffix alone would let it through.
            if image.is_file() and image.suffix.lower() in IMAGE_EXTS:
                bundled.append((str(image), f"_bundled/assets/{kind}"))

# Ship the company profile so the build is usable the moment it starts.
# The intern database is deliberately *not* bundled - it holds personal data
# and must not be baked into a binary that might be shared.
for name in ("company.json", "company.example.json"):
    profile = PROJECT / "company" / name
    if profile.is_file():
        bundled.append((str(profile), "_bundled/company"))

# --------------------------------------------------------------------------- #
# Third-party data / binaries that PyInstaller cannot infer
# --------------------------------------------------------------------------- #
datas = list(bundled)
binaries = []
hiddenimports = []

# customtkinter ships its widget themes and assets as data files.
datas += collect_data_files("customtkinter")

# reportlab needs its bundled AFM metrics and glyph data at runtime.
datas += collect_data_files("reportlab")

# tkcalendar resolves month/day names through babel's locale database.
for collector in ("babel", "pypdfium2", "pypdfium2_raw"):
    try:
        c_datas, c_binaries, c_hidden = collect_all(collector)
    except Exception:
        continue
    datas += c_datas
    binaries += c_binaries
    hiddenimports += c_hidden

hiddenimports += [
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.colorchooser",
    "tkcalendar",
    "babel.numbers",          # tkcalendar imports this lazily
    "darkdetect",             # customtkinter's system theme probe
    "PIL._tkinter_finder",
    # Reading and writing the intern/certificate export workbooks.
    "openpyxl",
    "openpyxl.workbook",
    "openpyxl.reader.excel",
    "openpyxl.writer.excel",
    "sqlite3",
]

icon = PROJECT / "assets" / "app.ico"

a = Analysis(
    ["app.py"],
    pathex=[str(PROJECT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim heavyweight libraries the app never imports.
    #
    # Do NOT exclude setuptools/pkg_resources here. PyInstaller installs a
    # pkg_resources runtime hook whenever that module is collected, and the
    # hook needs setuptools' vendored 'jaraco' package - excluding setuptools
    # makes the build die at startup with:
    #   ImportError: The 'jaraco' package is required
    # babel.messages is the gettext *tooling* (it is what pulls setuptools in);
    # tkcalendar only ever needs babel.dates / babel.numbers.
    excludes=[
        "matplotlib", "numpy", "scipy", "pandas", "pytest", "IPython",
        "notebook", "tornado", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "babel.messages", "sqlite3.test", "tkinter.test",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,          # GUI app: no console window unless diagnosing
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon) if icon.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
