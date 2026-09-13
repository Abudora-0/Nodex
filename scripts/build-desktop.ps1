# Builds the Nodex desktop installers (.msi and setup .exe) on Windows.
# Requires: Python 3.11+, Node 20+, Rust (stable-msvc) and the VS C++ build tools.
#   powershell -ExecutionPolicy Bypass -File scripts/build-desktop.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "1/4  Installing the engine and PyInstaller"
Push-Location "$root/backend"
python -m pip install --quiet -e . pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "2/4  Freezing the engine sidecar"
python -m PyInstaller packaging/nodex-sidecar.spec --noconfirm --distpath build/sidecar --workpath build/pyi
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Pop-Location

Write-Host "3/4  Placing the sidecar where Tauri expects it"
$triple = ((rustc -vV) | Select-String "^host:").ToString().Split(" ")[1].Trim()
$binaries = "$root/frontend/src-tauri/binaries"
New-Item -ItemType Directory -Force $binaries | Out-Null
Copy-Item "$root/backend/build/sidecar/nodex-sidecar.exe" "$binaries/nodex-sidecar-$triple.exe" -Force

Write-Host "4/4  Building the app and installers"
Push-Location "$root/frontend"
npm ci
if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
npx tauri build
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
Pop-Location

Write-Host ""
Write-Host "Installers are in frontend/src-tauri/target/release/bundle/"
