# Buduje DietBot.exe (folder dist\DietBot). Uruchom z katalogu projektu:  powershell -File packaging\zbuduj.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
Push-Location web
npm ci
npm run build
Pop-Location
.\.venv\Scripts\python.exe packaging\make_icon.py
.\.venv\Scripts\python.exe -m PyInstaller packaging\DietBot.spec --noconfirm --distpath dist --workpath build
Write-Host "Gotowe: dist\DietBot\DietBot.exe (dane startowe są dołączone do paczki)."
