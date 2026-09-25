"""Natywne okno Windows „Zapisz jako” dla eksportów aplikacji."""

from __future__ import annotations

import os
from pathlib import Path


def save_text_as(default_name: str, title: str, content: str) -> Path | None:
    """Pozwala użytkownikowi wybrać nazwę i miejsce, po czym zapisuje UTF-8 z BOM.

    Zwraca None, gdy użytkownik zamknął okno bez zapisywania.
    """
    if os.name != "nt":
        raise RuntimeError("Okno 'Zapisz jako' jest obecnie dostępne w aplikacji Windows.")

    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    downloads = Path.home() / "Downloads"
    initial = downloads / default_name if downloads.is_dir() else Path(default_name)
    buffer = ctypes.create_unicode_buffer(str(initial), 32768)
    dialog = OPENFILENAMEW()
    dialog.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    get_foreground_window = ctypes.windll.user32.GetForegroundWindow
    get_foreground_window.restype = wintypes.HWND
    dialog.hwndOwner = get_foreground_window()
    dialog.lpstrFilter = "Pliki tekstowe (*.txt)\0*.txt\0Wszystkie pliki (*.*)\0*.*\0\0"
    dialog.nFilterIndex = 1
    dialog.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(buffer)
    dialog.lpstrTitle = title
    dialog.lpstrDefExt = "txt"
    dialog.Flags = 0x00000002 | 0x00000004 | 0x00000008 | 0x00000800 | 0x00080000

    get_save_file_name = ctypes.windll.comdlg32.GetSaveFileNameW
    get_save_file_name.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    get_save_file_name.restype = wintypes.BOOL
    if not get_save_file_name(ctypes.byref(dialog)):
        error = ctypes.windll.comdlg32.CommDlgExtendedError()
        if error == 0:
            return None
        raise OSError(f"Okno zapisu zakończyło się błędem 0x{error:04X}.")

    path = Path(buffer.value)
    if not path.suffix:
        path = path.with_suffix(".txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8-sig")
    return path
