# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PhantomGit.

Produces a single executable that:
  - Acts as the installer/CLI when invoked normally (e.g. `phantomgit install`).
  - Acts as the background service when invoked with `--service`
    (the system service manager calls it this way).

Build with:
    pyinstaller phantomgit.spec
"""
import sys

block_cipher = None

# Hidden imports: PyInstaller's static analyzer misses these because they are
# either imported lazily (only when a specific AI provider is selected) or
# imported by string.
hidden_imports = [
    "requests",
    "psutil",
    # google-genai is optional; included so users who choose Gemini don't
    # need to install anything else.
    "google.genai",
    "google.genai.client",
    "google.genai.models",
    # watchdog and its OS-specific observer backends. PyInstaller's static
    # analyzer cannot see these because Observer picks the backend at runtime.
    "watchdog",
    "watchdog.observers",
    "watchdog.observers.api",
    "watchdog.observers.polling",
    "watchdog.observers.inotify",       # Linux
    "watchdog.observers.fsevents",      # macOS
    "watchdog.observers.read_directory_changes",  # Windows
    "watchdog.events",
]

a = Analysis(
    ["install.py"],
    pathex=[],
    binaries=[],
    datas=[
        # Bundle Main.py so the --service mode can import it
        ("Main.py", "."),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim things we never use to keep the binary smaller
        "tkinter", "test", "unittest", "pydoc",
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Uncomment to add an icon (place icon files in the repo root):
    # icon="icon.ico",  # Windows
    # icon="icon.icns", # macOS
)
