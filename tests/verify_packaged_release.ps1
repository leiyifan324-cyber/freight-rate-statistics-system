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
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Packaged executable was not found: $executable"
}
if (-not (Test-Path -LiteralPath $rulesFile)) {
    throw "Packaged rules file was not found: $rulesFile"
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
    } | Format-Table -AutoSize
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit(10000) | Out-Null
    }
}
