# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PhantomGit.
Produces a single-file executable that:
  - Acts as the installer/CLI when invoked normally.
  - Acts as the background service when invoked with --service.

Build with:
    pyinstaller phantomgit.spec

SPECPATH is defined by PyInstaller as the directory containing this file,
so the build works correctly regardless of the working directory.
"""
import sys
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# ── Absolute paths anchored to the spec file location ─────────────────────────
# SPECPATH is set by PyInstaller to the directory that contains this .spec file.
# Using it here means `pyinstaller phantomgit.spec` works from any directory,
# not just from the repo root.
REPO_ROOT = SPECPATH  # noqa: F821  (PyInstaller injects SPECPATH)

# ── Hidden imports ─────────────────────────────────────────────────────────────
# PyInstaller's static analyser misses these because they are imported lazily
# (AI provider chosen at runtime) or selected dynamically (watchdog backend).
hidden_imports = [
    # Core runtime deps
    "requests",
    "psutil",
    # main.py — compiled as a frozen module; install.py imports it with --service
    "main",
]

# google-genai: use collect_submodules so we don't hard-code internal paths that
# change between SDK versions.  Wrapped in try/except so builds without the
# package installed don't fail (it's an optional AI provider).
try:
    hidden_imports += collect_submodules("google.genai")
except Exception:
    pass

# watchdog: each OS ships a different observer backend.
# collect_submodules only picks up modules that are actually importable on the
# current platform, so inotify won't be attempted on macOS and vice-versa.
try:
    hidden_imports += collect_submodules("watchdog")
except Exception:
    hidden_imports += ["watchdog", "watchdog.observers", "watchdog.events",
                       "watchdog.observers.polling"]  # polling works everywhere

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    # Entry-point script — absolute path avoids CWD ambiguity.
    [os.path.join(REPO_ROOT, "install.py")],
    # Tell PyInstaller to search the repo root for Python modules, specifically
    # so it finds and compiles main.py (included via hidden_imports above).
    pathex=[REPO_ROOT],
    binaries=[],
    # datas is for NON-Python files (icons, certs, etc.).
    # Python source files must NOT go here; they belong in hiddenimports so
    # they are compiled and frozen — not copied as raw .py files.
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Reduce binary size by excluding things PhantomGit never uses.
        "tkinter",
        "test",
        "unittest",
        "pydoc",
        "doctest",
        "xml.dom",
        "xml.sax",
    ],
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
    name="phantomgit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX compression is disabled on non-Windows due to known compatibility
    # problems (flagged as malware by some AV, broken on newer glibc).
    upx=(sys.platform == "win32"),
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,  # macOS: don't convert argv to Apple Events
    target_arch=None,      # None = native arch; set to "universal2" for fat binaries
    codesign_identity=None,
    entitlements_file=None,
    # Uncomment after adding icon files to the repo root:
    # icon=os.path.join(REPO_ROOT, "icon.ico"),   # Windows
    # icon=os.path.join(REPO_ROOT, "icon.icns"),  # macOS
)
