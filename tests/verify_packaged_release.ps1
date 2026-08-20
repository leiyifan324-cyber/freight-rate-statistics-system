param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir,
    [Parameter(Mandatory = $true)]
    [string]$TestRoot,
    [int]$Port = 8876
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$appDir = [IO.Path]::GetFullPath($AppDir)
$testRoot = [IO.Path]::GetFullPath($TestRoot)
$executable = Join-Path $appDir "FreightQuoteSystem.exe"
$rulesFile = Join-Path $appDir "freight_rules.json"
$napcatRoot = Join-Path $appDir "third_party\NapCatQQ"
$napcatManifestPath = Join-Path $napcatRoot "napcat-shell-windows-node.json"
$napcatLicensePath = Join-Path $napcatRoot "NAPCAT_LICENSE.txt"
$napcatInstallerPath = Join-Path $appDir "安装NapCatQQ.ps1"
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Packaged executable was not found: $executable"
}
if (-not (Test-Path -LiteralPath $rulesFile)) {
    throw "Packaged rules file was not found: $rulesFile"
}
foreach ($requiredPath in @($napcatManifestPath, $napcatLicensePath, $napcatInstallerPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Packaged NapCatQQ component was not found: $requiredPath"
    }
}
$napcatManifest = Get-Content -LiteralPath $napcatManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$napcatArchivePath = Join-Path $napcatRoot ([string]$napcatManifest.asset_name)
if (-not (Test-Path -LiteralPath $napcatArchivePath)) {
    throw "Packaged NapCatQQ archive was not found: $napcatArchivePath"
}
$napcatHash = (Get-FileHash -LiteralPath $napcatArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($napcatHash -ne ([string]$napcatManifest.sha256).ToLowerInvariant()) {
    throw "Packaged NapCatQQ archive hash does not match its manifest."
}

$napcatInstallRoot = Join-Path $testRoot "NapCatQQ"
& $napcatInstallerPath -InstallRoot $napcatInstallRoot -DoNotStart
$stableNapcatLauncher = Join-Path $napcatInstallRoot "Start-NapCat.cmd"
$versionedNapcatLauncher = Join-Path $napcatInstallRoot "$($napcatManifest.version)\napcat\launcher.bat"
if (-not (Test-Path -LiteralPath $stableNapcatLauncher) -or -not (Test-Path -LiteralPath $versionedNapcatLauncher)) {
    throw "NapCatQQ installer did not produce the expected launchers."
}
if (Get-ChildItem -LiteralPath $napcatInstallRoot -Recurse -File -Filter "QQ.exe" -ErrorAction SilentlyContinue) {
    throw "NapCatQQ installation unexpectedly contains QQ.exe."
}

New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$configPath = Join-Path $testRoot "qq_test_config.json"
$outputRoot = Join-Path $testRoot "output"
$config = [ordered]@{
    ws_url = "ws://127.0.0.1:3001"
    access_token = ""
    napcat_launcher = ""
    group_ids = @()
    group_names = @{}
    group_default_origins = @{}
    output_root = $outputRoot
    rules_file = $rulesFile
    accept_self_messages = $true
    ocr_enabled = $false
    status_dashboard = [ordered]@{
        enabled = $true
        host = "127.0.0.1"
        port = $Port
    }
    backup_retention_days = 30
    time_check_urls = @()
    reconnect_seconds = 5
    rebuild_on_start = $true
}
[IO.File]::WriteAllText(
    $configPath,
    ($config | ConvertTo-Json -Depth 8),
    [Text.UTF8Encoding]::new($false)
)

$process = Start-Process -FilePath $executable -ArgumentList @(
    "--mode", "qq-live", "--config", $configPath
) -PassThru
try {
    $status = $null
    $managerConfig = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 2
            $managerConfig = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/config" -TimeoutSec 2
            break
        }
        catch {
            if ($process.HasExited) {
                throw "Packaged process exited before the dashboard became ready."
            }
        }
    }
    if ($null -eq $status) {
        throw "Packaged dashboard did not become ready."
    }
    if ($status.connection -ne "setup_required") {
        throw "Unexpected first-run state: $($status.connection)"
    }
    if (@($managerConfig.groups).Count -ne 0) {
        throw "First-run manager should start without configured groups."
    }
    Write-Host "PACKAGED_FIRST_RUN_OK"
    [pscustomobject]@{
        Connection = $status.connection
        Port = $Port
        GroupCount = @($managerConfig.groups).Count
        ProcessId = $process.Id
        NapCatVersion = $napcatManifest.version
        NapCatIncludesQQ = $napcatManifest.contains_qq
        NapCatInstallVerified = $true
    } | Format-Table -AutoSize
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit(10000) | Out-Null
    }
}
