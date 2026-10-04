$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

& (Join-Path $PSScriptRoot "zbuduj.ps1")

$Candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)
$Compiler = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Compiler) {
    $OnPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($OnPath) { $Compiler = $OnPath.Source }
}
if (-not $Compiler) {
    throw "Nie znaleziono Inno Setup 6. Zainstaluj: winget install --id JRSoftware.InnoSetup --exact"
}

& $Compiler (Join-Path $PSScriptRoot "DietBot.iss")
if ($LASTEXITCODE -ne 0) { throw "Budowanie instalatora nie powiodlo sie." }
Write-Host "Gotowe: release\Porcja-Setup-0.4.0.exe"
