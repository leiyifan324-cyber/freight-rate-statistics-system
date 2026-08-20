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
$thirdPartyRoot = Join-Path $repoRoot "third_party"
$napcatManifestPath = Join-Path $thirdPartyRoot "napcat-shell-windows-node.json"

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

function Convert-BatchFilesToWindowsFormat([string]$Root) {
    $asciiEncoding = [Text.ASCIIEncoding]::new()
    $batchFiles = Get-ChildItem -LiteralPath $Root -Recurse -File |
        Where-Object { $_.Extension -in ".bat", ".cmd" }
    foreach ($batchFile in $batchFiles) {
        $content = [IO.File]::ReadAllText($batchFile.FullName)
        if ($content.ToCharArray() | Where-Object { [int]$_ -gt 127 } | Select-Object -First 1) {
            throw "Windows command script must contain ASCII text only: $($batchFile.FullName)"
        }
        $normalized = $content.Replace("`r`n", "`n").Replace("`r", "`n")
        $windowsContent = $normalized.Replace("`n", "`r`n")
        [IO.File]::WriteAllText($batchFile.FullName, $windowsContent, $asciiEncoding)
    }
}

Reset-SafeDirectory $buildRoot
Reset-SafeDirectory $releaseRoot

if (-not (Test-Path -LiteralPath $napcatManifestPath)) {
    throw "NapCatQQ release manifest was not found: $napcatManifestPath"
}
$napcatManifest = Get-Content -LiteralPath $napcatManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$napcatCacheRoot = Join-Path $repoRoot ".release-cache"
New-Item -ItemType Directory -Path $napcatCacheRoot -Force | Out-Null
$napcatCache = Join-Path $napcatCacheRoot ("NapCat.Shell.Windows.Node-" + $napcatManifest.version + ".zip")
$expectedNapcatHash = ([string]$napcatManifest.sha256).ToLowerInvariant()
$downloadNapcat = $true
if (Test-Path -LiteralPath $napcatCache) {
    $cachedHash = (Get-FileHash -LiteralPath $napcatCache -Algorithm SHA256).Hash.ToLowerInvariant()
    $downloadNapcat = $cachedHash -ne $expectedNapcatHash
}
if ($downloadNapcat) {
    $partialNapcat = $napcatCache + ".download"
    [IO.File]::Delete($partialNapcat)
    Invoke-WebRequest -Uri ([string]$napcatManifest.download_url) -OutFile $partialNapcat
    $downloadedHash = (Get-FileHash -LiteralPath $partialNapcat -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($downloadedHash -ne $expectedNapcatHash) {
        [IO.File]::Delete($partialNapcat)
        throw "NapCatQQ archive SHA-256 mismatch. Expected $expectedNapcatHash, got $downloadedHash"
    }
    [IO.File]::Move($partialNapcat, $napcatCache, $true)
}

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
Convert-BatchFilesToWindowsFormat $staging

$napcatStaging = Join-Path $staging "third_party\NapCatQQ"
New-Item -ItemType Directory -Path $napcatStaging -Force | Out-Null
Copy-Item -LiteralPath $napcatManifestPath,(Join-Path $thirdPartyRoot "NAPCAT_LICENSE.txt"),(Join-Path $thirdPartyRoot "NAPCAT_SOURCE.txt") -Destination $napcatStaging
Copy-Item -LiteralPath $napcatCache -Destination (Join-Path $napcatStaging ([string]$napcatManifest.asset_name))

Add-Type -AssemblyName System.IO.Compression.FileSystem
$napcatArchive = [IO.Compression.ZipFile]::OpenRead((Join-Path $napcatStaging ([string]$napcatManifest.asset_name)))
try {
    $entryNames = @($napcatArchive.Entries | ForEach-Object { $_.FullName.Replace('\', '/').ToLowerInvariant() })
    if ($entryNames -contains "qq.exe" -or @($entryNames | Where-Object { $_.EndsWith("/qq.exe") }).Count -gt 0) {
        throw "The NapCatQQ archive contains QQ.exe; publishing was stopped."
    }
    foreach ($requiredEntry in @("napcat/launcher.bat", "napcat/launcher-win10.bat")) {
        if ($entryNames -notcontains $requiredEntry) {
            throw "The NapCatQQ archive is missing $requiredEntry"
        }
    }
}
finally {
    $napcatArchive.Dispose()
}

$packageTestRoot = Join-Path $buildRoot "packaged-smoke-test"
& (Join-Path $repoRoot "tests\verify_packaged_release.ps1") -AppDir $staging -TestRoot $packageTestRoot
if ($LASTEXITCODE -ne 0) {
    throw "Packaged release smoke test failed"
}

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
