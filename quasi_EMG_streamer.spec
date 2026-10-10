"""Build the portable Windows application with its scientific dependencies."""
from pathlib import Path
import shutil

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
datas = [(str(path), "schedules") for path in (root / "schedules").glob("*_10_14_16_18_*_blocks.csv")]
datas += [(str(root / "schedules" / "sources.json"), "schedules")]
# MNE uses lazy imports and .pyi declarations at runtime.
datas += collect_data_files("mne", include_py_files=True, excludes=["**/tests/**"])
hiddenimports = collect_submodules("mne", filter=lambda name: "tests" not in name.split("."))
for package in ("mne", "numpy", "scipy", "matplotlib", "PyQt6", "pyqtgraph", "pyxdf", "threadpoolctl"):
    datas += copy_metadata(package, recursive=True)

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["QtAgg", "Agg"]}},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide2", "PySide6", "IPython", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="quasi_EMG_streamer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="quasi_EMG_streamer")

destination = Path(DISTPATH) / "quasi_EMG_streamer"
shutil.copy2(root / "quasi_emg_settings.json", destination)
shutil.copy2(root / "README-Windows.txt", destination)
shutil.copy2(root / "README.md", destination)
(destination / "docs").mkdir(exist_ok=True)
shutil.copy2(root / "docs" / "online_monitoring.md", destination / "docs")
