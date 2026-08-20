[CmdletBinding()]
param(
    [string]$InstallRoot = "",
    [switch]$DoNotStart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "NapCatQQ Shell Windows Node requires 64-bit Windows."
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$vendorRoot = Join-Path $scriptRoot "third_party\NapCatQQ"
$manifestPath = Join-Path $vendorRoot "napcat-shell-windows-node.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "NapCatQQ release manifest is missing: $manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$archivePath = Join-Path $vendorRoot ([string]$manifest.asset_name)
if (-not (Test-Path -LiteralPath $archivePath)) {
    throw "Bundled NapCatQQ archive is missing: $archivePath"
}

$expectedHash = ([string]$manifest.sha256).ToLowerInvariant()
$actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
    throw "NapCatQQ archive SHA-256 mismatch. Expected $expectedHash, got $actualHash."
}

if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA "NapCatQQ"
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$versionDirectory = Join-Path $InstallRoot ([string]$manifest.version)
New-Item -ItemType Directory -Path $versionDirectory -Force | Out-Null

Write-Host "Installing the verified NapCatQQ $($manifest.version) package..."
Expand-Archive -LiteralPath $archivePath -DestinationPath $versionDirectory -Force

$unexpectedQQ = Get-ChildItem -LiteralPath $versionDirectory -Recurse -File -Filter "QQ.exe" -ErrorAction SilentlyContinue
if ($unexpectedQQ) {
    throw "The bundled NapCatQQ package unexpectedly contains QQ.exe. Installation was stopped."
}

$launcher = Join-Path $versionDirectory "napcat\launcher.bat"
$win10Launcher = Join-Path $versionDirectory "napcat\launcher-win10.bat"
if (-not (Test-Path -LiteralPath $launcher) -or -not (Test-Path -LiteralPath $win10Launcher)) {
    throw "NapCatQQ launchers were not found after extraction."
}

$version = [string]$manifest.version
$stableLauncher = Join-Path $InstallRoot "Start-NapCat.cmd"
$launcherLines = @(
    "@echo off",
    "setlocal EnableExtensions",
    "for /f %%B in ('powershell.exe -NoProfile -Command `[Environment`]::OSVersion.Version.Build') do set `"WINDOWS_BUILD=%%B`"",
    "if %WINDOWS_BUILD% LSS 22000 (",
    "    call `"%~dp0$version\napcat\launcher-win10.bat`"",
    ") else (",
    "    call `"%~dp0$version\napcat\launcher.bat`"",
    ")"
)
$launcherContent = ($launcherLines -join "`r`n") + "`r`n"
[IO.File]::WriteAllText($stableLauncher, $launcherContent, [Text.ASCIIEncoding]::new())
[IO.File]::WriteAllText(
    (Join-Path $InstallRoot "installed-release.json"),
    ($manifest | ConvertTo-Json -Depth 8),
    [Text.UTF8Encoding]::new($false)
)

Write-Host "NapCatQQ installed at: $versionDirectory"
Write-Host "Stable launcher: $stableLauncher"
Write-Host "Tencent QQ is not included. Install the official 64-bit QQ before logging in."

if (-not $DoNotStart) {
    Start-Process -FilePath $env:COMSPEC -ArgumentList @("/c", "`"$stableLauncher`"") -WorkingDirectory $InstallRoot
}
