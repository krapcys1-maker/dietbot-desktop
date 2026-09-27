# Buduje DietBot.exe (folder dist\DietBot). Uruchom z katalogu projektu:  powershell -File packaging\zbuduj.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
Push-Location web
npm ci
if ($LASTEXITCODE -ne 0) { throw "Instalacja zaleznosci interfejsu nie powiodla sie." }
npm run build
if ($LASTEXITCODE -ne 0) { throw "Budowanie interfejsu nie powiodlo sie." }
Pop-Location
.\.venv\Scripts\python.exe packaging\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Tworzenie ikony nie powiodlo sie." }
.\.venv\Scripts\python.exe -m PyInstaller packaging\DietBot.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "Budowanie EXE nie powiodlo sie." }
Write-Host "Gotowe: dist\DietBot\DietBot.exe (dane startowe są dołączone do paczki)."
