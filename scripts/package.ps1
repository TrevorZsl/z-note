param(
    [string]$Python = "python",
    [switch]$Installer,
    [string]$Iscc = ""
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $env:PYTHONPATH = "$projectRoot\.runtime-deps;$projectRoot;$env:PYTHONPATH"
    $version = & $Python -c "from version import APP_VERSION; from updater import parse_version; parse_version(APP_VERSION); print(APP_VERSION)"
    if ($LASTEXITCODE -ne 0) { throw "Invalid application version" }
    if ($Installer) {
        if (-not $Iscc) {
            $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
            if ($compiler) { $Iscc = $compiler.Source }
            else { $Iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe" }
        }
        if (-not (Test-Path -LiteralPath $Iscc -PathType Leaf)) {
            throw "Install Inno Setup 6 first, or pass -Iscc <path to ISCC.exe>"
        }
    }
    & $Python -m py_compile main.py storage.py bundled_fonts.py resize_surface.py updater.py version.py
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
    if ($Installer) {
        & $Iscc "/DAppVersion=$version" "installer\z-note.iss"
        if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed" }
        $installerName = "z-note-setup-$version.exe"
        $hash = (Get-FileHash -LiteralPath (Join-Path "dist" $installerName) -Algorithm SHA256).Hash.ToLowerInvariant()
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText((Join-Path $projectRoot "dist\SHA256SUMS"), "$hash  $installerName`n", $utf8)
        Write-Host "Installer and SHA256SUMS are ready in dist."
    }
} finally {
    Pop-Location
}
