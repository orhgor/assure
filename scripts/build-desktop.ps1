# Build Assure.exe with PyInstaller. Output: dist\Assure\Assure.exe
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Activate = Join-Path $Root "prompt_matrix\.venv\Scripts\Activate.ps1"
if (-not (Test-Path $Activate)) {
    Write-Error "Run scripts\install.ps1 first."
}
. $Activate
python -m pip install -U pyinstaller
pyinstaller --noconfirm --clean (Join-Path $Root "packaging\assure.spec")
Write-Host ""
Write-Host "Built. Double-click dist\Assure\Assure.exe, then open http://127.0.0.1:8765"
Write-Host "There is no public download URL yet."
