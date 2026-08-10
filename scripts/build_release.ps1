param(
    [string]$Version = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Version) {
    $Version = (Get-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must use MAJOR.MINOR.PATCH: $Version"
}

$buildRoot = Join-Path $repoRoot "build"
$releaseRoot = Join-Path $repoRoot "dist-release"
$pyInstallerDist = Join-Path $buildRoot "pyinstaller-dist"
$pyInstallerWork = Join-Path $buildRoot "pyinstaller-work"
$portableRoot = Join-Path $buildRoot "portable"
$staging = Join-Path $portableRoot "FreightQuoteSystem"

function Reset-SafeDirectory([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullRepo = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($fullRepo, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean path outside repository: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $fullPath | Out-Null
}

Reset-SafeDirectory $buildRoot
Reset-SafeDirectory $releaseRoot

$env:FREIGHT_PYTHON = $Python
& (Join-Path $PSScriptRoot "run_tests.ps1")

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name FreightQuoteSystem `
    --distpath $pyInstallerDist `
    --workpath $pyInstallerWork `
    --specpath $buildRoot `
    --paths (Join-Path $repoRoot "src") `
    --collect-all matplotlib `
    --hidden-import websocket `
    (Join-Path $repoRoot "src\freight_app.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed"
}

New-Item -ItemType Directory -Path $staging | Out-Null
Copy-Item -Path (Join-Path $pyInstallerDist "FreightQuoteSystem\*") -Destination $staging -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot "config\qq_live_config.example.json") -Destination (Join-Path $staging "qq_live_config.json")
Copy-Item -LiteralPath (Join-Path $repoRoot "config\freight_rules.default.json") -Destination (Join-Path $staging "freight_rules.json")
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\windows_ocr.ps1") -Destination (Join-Path $staging "windows_ocr.ps1")
Copy-Item -LiteralPath (Join-Path $repoRoot "README.md"),(Join-Path $repoRoot "LICENSE"),(Join-Path $repoRoot "THIRD_PARTY_NOTICES.md"),(Join-Path $repoRoot "CHANGELOG.md") -Destination $staging
Copy-Item -LiteralPath (Join-Path $repoRoot "docs") -Destination (Join-Path $staging "docs") -Recurse
Copy-Item -Path (Join-Path $repoRoot "installer\assets\*") -Destination $staging

$portableZip = Join-Path $releaseRoot "FreightQuoteSystem-Portable-v$Version.zip"
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $portableZip -CompressionLevel Optimal

$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 compiler was not found"
}
& $iscc "/DAppVersion=$Version" "/DSourceDir=$staging" "/DOutputDir=$releaseRoot" (Join-Path $repoRoot "installer\FreightQuoteSystem.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup build failed"
}

$assets = Get-ChildItem -LiteralPath $releaseRoot -File | Where-Object { $_.Extension -in ".exe", ".zip" } | Sort-Object Name
$checksumLines = foreach ($asset in $assets) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $asset.FullName).Hash.ToLowerInvariant()
    "$hash  $($asset.Name)"
}
[IO.File]::WriteAllLines((Join-Path $releaseRoot "SHA256SUMS.txt"), $checksumLines, [Text.UTF8Encoding]::new($false))

Write-Host "RELEASE_BUILD_OK"
$assets | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
