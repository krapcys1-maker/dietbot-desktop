# -*- mode: python ; coding: utf-8 -*-
# Budowanie: .venv\Scripts\python -m PyInstaller packaging\DietBot.spec --noconfirm
# Wynik: dist\DietBot\DietBot.exe — uruchamiaj z folderu projektu (dane i konfiguracja są szukane obok/wyżej).
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

import os

ROOT = SPECPATH + r"\.."
# Dane startowe z czystego eksportu gita (zbuduj.ps1 ustawia PORCJA_DANE) — do paczki nie trafią pliki spoza
# repozytorium z katalogu roboczego (własne produkty, kopie, surowe bazy). Bez zmiennej: katalog projektu.
DATA_ROOT = os.environ.get("PORCJA_DANE") or ROOT
datas = [
    (ROOT + r"\web\dist", r"web\dist"),
    (DATA_ROOT + r"\konfiguracja", r"defaults\konfiguracja"),
]
# Dane startowe bez dane\raw: surowe bazy BLS/CIQUAL (ok. 34 MB) są potrzebne tylko komendom deweloperskim
# (importuj-bls, odswiez-bls), a paczka kopiuje defaults\dane do katalogu danych każdego użytkownika.
for sub in ("dania", "produkty", "sklepy", "korekty"):
    datas.append((DATA_ROOT + "\\dane\\" + sub, "defaults\\dane\\" + sub))
binaries = []
hiddenimports = []
for pkg in ("ortools", "webview", "uvicorn", "simplemma", "rapidfuzz", "aiogram", "clr_loader", "pythonnet"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("dietbot")
datas += collect_data_files("certifi")

a = Analysis(
    [ROOT + r"\packaging\dietbot_app.py"],
    pathex=[ROOT + r"\src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DietBot",
    icon=ROOT + r"\packaging\dietbot.ico",
    version=ROOT + r"\packaging\version_info.txt",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DietBot")
