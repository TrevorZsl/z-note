param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $env:PYTHONPATH = "$projectRoot\.runtime-deps;$projectRoot;$env:PYTHONPATH"
    & $Python -m py_compile main.py storage.py bundled_fonts.py
    if ($LASTEXITCODE -ne 0) { throw "Syntax check failed" }
    & $Python -c "import main"
    if ($LASTEXITCODE -ne 0) { throw "Import check failed" }
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name z-note `
        --specpath build --icon "$projectRoot/assets/icons/app.ico" --add-data "$projectRoot/assets;assets" `
        --hidden-import PIL._tkinter_finder --collect-submodules PIL --collect-binaries PIL `
        --exclude-module numpy --exclude-module pandas --exclude-module matplotlib `
        --exclude-module scipy --exclude-module PyQt5 --exclude-module PyQt6 `
        --exclude-module PySide2 --exclude-module PySide6 --exclude-module fontTools main.py
    if ($LASTEXITCODE -ne 0) { throw "Packaging failed" }
} finally {
    Pop-Location
}
