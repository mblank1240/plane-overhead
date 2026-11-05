# PyInstaller build for Windows, macOS and Linux:  pyinstaller plane-overhead.spec
#
# One-folder build (not --onefile): single-file executables unpack themselves to a
# temp dir at launch, which antivirus treats as suspicious. Builds are unsigned.
#
# Output:  Windows  dist/PlaneOverhead/PlaneOverhead.exe
#          macOS    dist/Plane Overhead.app
#          Linux    dist/PlaneOverhead/PlaneOverhead  (the UI opens in the default browser)

import sys

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("desktop_notifier") + collect_submodules("pystray")
if sys.platform == "win32":
    hidden += collect_submodules("winrt")

a = Analysis(
    ["main.py"],
    datas=[("web", "web"), ("assets", "assets")],
    hiddenimports=hidden,
    # No webview backend is bundled on Linux (GTK/Qt don't bundle reliably); window.py
    # falls back to the default browser there.
    excludes=["tkinter"] + (["webview"] if sys.platform.startswith("linux") else []),
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="PlaneOverhead",
    console=False,
    icon="assets/icon.ico" if sys.platform == "win32" else "assets/icon.png",
    upx=False,  # UPX-packed binaries are another antivirus red flag
)
coll = COLLECT(exe, a.binaries, a.datas, name="PlaneOverhead", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Plane Overhead.app",
        icon="assets/icon.png",
        bundle_identifier="net.planeoverhead.app",
        info_plist={
            "CFBundleShortVersionString": "2.1.0",
            "LSUIElement": True,  # menu-bar app; the window process adds its own Dock icon
            "NSHighResolutionCapable": True,
        },
    )
