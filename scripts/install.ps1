# Install Assure from the product source (this repo). Not the webpage GitHub repo.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$PyExe = $null
$PyPrefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PyExe = "py"
    $PyPrefix = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PyExe = "python"
}
if (-not $PyExe) {
    Write-Error "Need Python 3.10 or newer."
}

$ver = & $PyExe @PyPrefix -c "import sys; print('%d.%d' % (sys.version_info[0], sys.version_info[1]))"
$parts = $ver.Trim().Split(".")
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
    Write-Error "Need Python 3.10 or newer. Found $ver."
}

$Venv = Join-Path $Root "prompt_matrix\.venv"
if (-not (Test-Path $Venv)) {
    Write-Host "Creating venv with $PyExe $($PyPrefix -join ' ')"
    & $PyExe @PyPrefix -m venv $Venv
}
$Activate = Join-Path $Venv "Scripts\Activate.ps1"
. $Activate
python -m pip install -U pip
pip install -e $Root

Write-Host ""
Write-Host "Installed. Run:"
Write-Host "  prompt_matrix\.venv\Scripts\Activate.ps1"
Write-Host "  assure --web"
Write-Host ""
Write-Host "Your browser should open. First run: paste a provider key, then write a question."
Write-Host "If the browser does not open, go to http://127.0.0.1:8765"
Write-Host "pip install prompt-matrix is not on PyPI yet."
Write-Host "github.com/orhgor/assure is the public site only. Do not clone it for the app."
