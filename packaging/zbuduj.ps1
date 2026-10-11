# Buduje DietBot.exe (folder dist\DietBot). Uruchom z katalogu projektu:  powershell -File packaging\zbuduj.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
Push-Location web
npm ci
if ($LASTEXITCODE -ne 0) { throw "Instalacja zaleznosci interfejsu nie powiodla sie." }
npm run build
if ($LASTEXITCODE -ne 0) { throw "Budowanie interfejsu nie powiodlo sie." }
Pop-Location
# Dane startowe tylko z zatwierdzonego stanu gita (bez plików spoza repozytorium z katalogu roboczego)
$Dirty = git status --porcelain -- dane konfiguracja
if ($Dirty) { throw "Niezatwierdzone zmiany w dane/ lub konfiguracja/ — zatwierdź je przed budowaniem:`n$Dirty" }
$Clean = Join-Path (Get-Location) "build\czyste_dane"
if (Test-Path $Clean) { Remove-Item -Recurse -Force $Clean }
New-Item -ItemType Directory -Force $Clean | Out-Null
$Tar = Join-Path $Clean "dane.tar"
git archive --format=tar -o $Tar HEAD dane konfiguracja
if ($LASTEXITCODE -ne 0) { throw "Eksport danych z gita nie powiodl sie." }
tar -xf $Tar -C $Clean
if ($LASTEXITCODE -ne 0) { throw "Rozpakowanie danych nie powiodlo sie." }
Remove-Item $Tar
$env:PORCJA_DANE = $Clean
.\.venv\Scripts\python.exe packaging\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Tworzenie ikony nie powiodlo sie." }
.\.venv\Scripts\python.exe -m PyInstaller packaging\DietBot.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "Budowanie EXE nie powiodlo sie." }
Write-Host "Gotowe: dist\DietBot\DietBot.exe (dane startowe są dołączone do paczki)."
