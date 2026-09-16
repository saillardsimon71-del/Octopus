"""Construit Podalux.exe (PyInstaller, onefile, windowed)."""
import PyInstaller.__main__

PyInstaller.__main__.run([
    "run_gui.py",
    "--onefile",
    "--windowed",
    "--name", "Podalux",
    "--noconfirm",
    "--clean",
    "--collect-all", "customtkinter",
])
