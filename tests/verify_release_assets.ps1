param(
    [Parameter(Mandatory=$true)][string]$ReleaseDir,
    [Parameter(Mandatory=$true)][string]$TestRoot,
    [int]$Port = 18976,
    [switch]$DisposableMachine
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$release = [IO.Path]::GetFullPath($ReleaseDir)
$root = [IO.Path]::GetFullPath($TestRoot)
if (Test-Path -LiteralPath $root) { throw "TestRoot must be a NEW, isolated directory: $root" }
New-Item -ItemType Directory -Path $root | Out-Null
$results = [Collections.Generic.List[object]]::new()
function Check([string]$Name, [bool]$Passed) {
    $results.Add([pscustomobject]@{name=$Name; passed=$Passed})
    if (-not $Passed) { throw "FAILED: $Name" }
    Write-Host "PASS: $Name"
}
function Verify-Files([string]$Directory, $Manifest) {
    foreach ($entry in $Manifest.files.PSObject.Properties) {
        $path = [IO.Path]::GetFullPath((Join-Path $Directory $entry.Name))
        if (-not $path.StartsWith($Directory.TrimEnd('\')+'\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe manifest path' }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing file: $($entry.Name)" }
        if ((Get-Item -LiteralPath $path).Length -ne $entry.Value.size) { throw "Size mismatch: $($entry.Name)" }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.Value.sha256) { throw "Hash mismatch: $($entry.Name)" }
    }
}
$registrySubkey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{F82C2ED2-B729-4E1E-99EF-59C50DC31C13}_is1'
$registryKey = 'HKCU:\'+$registrySubkey
$nativeKey = 'HKCU\'+$registrySubkey
$hadRegistry = Test-Path -LiteralPath $registryKey
$registryBackup = Join-Path $root 'previous-install-registry.reg'
if ($hadRegistry) {
    & reg.exe export $nativeKey $registryBackup /y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot back up existing installer registration' }
}
$installed = Join-Path $root (([string][char]0x5b89)+([string][char]0x88c5)+' installed app')
$oldPath = $env:PATH
$oldPythonPath = $env:PYTHONPATH
try {
    $lines = Get-Content -LiteralPath (Join-Path $release 'SHA256SUMS.txt')
    foreach ($line in $lines) {
        if ($line -notmatch '^([a-fA-F0-9]{64})\s+([^\\/]+)$') { throw "Invalid checksum line: $line" }
        $digest=$Matches[1]; $name=$Matches[2]
        Check "Release asset SHA256: $name" ((Get-FileHash -LiteralPath (Join-Path $release $name) -Algorithm SHA256).Hash -eq $digest)
    }
    $zips = @(Get-ChildItem -LiteralPath $release -Filter 'FreightQuoteSystem-Portable-v*.zip')
    $setups = @(Get-ChildItem -LiteralPath $release -Filter 'FreightQuoteSystem-Setup-v*.exe')
    Check 'Exactly one portable and setup' ($zips.Count -eq 1 -and $setups.Count -eq 1)
    $portable = Join-Path $root 'portable with spaces'
    Expand-Archive -LiteralPath $zips[0].FullName -DestinationPath $portable
    $manifest = Get-Content -LiteralPath (Join-Path $portable 'release-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Verify-Files $portable $manifest
    Check 'Portable entire manifest matches' $true
    $info = Get-Content -LiteralPath (Join-Path $portable 'build-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Check 'Build has clean committed source' ((-not $info.source_dirty) -and $info.source_commit -eq $manifest.source_commit)
    $cfg = Get-Content -LiteralPath (Join-Path $portable 'qq_live_config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Check 'Public defaults contain no groups, names or access token' (@($cfg.group_ids).Count -eq 0 -and @($cfg.group_names.PSObject.Properties).Count -eq 0 -and -not $cfg.access_token)
    Check 'Text-only is default' (-not $cfg.ocr_enabled)
    Check 'No packaged user databases' (@(Get-ChildItem -LiteralPath $portable -Recurse -File -Filter '*.db').Count -eq 0)
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $env:PYTHONPATH = ''
    $installArgs = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/NOCLOSEAPPLICATIONS','/TASKS=""',('/DIR="'+$installed+'"'),('/LOG="'+(Join-Path $root 'setup.log')+'"'))
    $setup = Start-Process -FilePath $setups[0].FullName -ArgumentList $installArgs -WindowStyle Hidden -PassThru -Wait
    Check 'Actual Setup exits successfully' ($setup.ExitCode -eq 0)
    Verify-Files $installed $manifest
    Check 'Installed files exactly match tested portable manifest' $true
    & (Join-Path $PSScriptRoot 'verify_packaged_release.ps1') -AppDir $installed -TestRoot (Join-Path $root 'installed smoke') -Port $Port
    Check 'Actual EXE first-run plus offline NapCat installation without Python on PATH' $?
    if ($DisposableMachine) {
        if ($env:CI -ne 'true' -or $hadRegistry) { throw 'Cold supervisor test requires a clean disposable CI machine' }
        $coldConfig = Join-Path $root 'cold-start-config.json'
        $cold = Get-Content -LiteralPath (Join-Path $installed 'qq_live_config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $cold.napcat_launcher = ''
        $cold.output_root = Join-Path $root 'cold-start-data'
        $cold.rules_file = Join-Path $installed 'freight_rules.json'
        $cold.status_dashboard.port = $Port + 1
        $cold.time_check_urls = @()
        [IO.File]::WriteAllText($coldConfig, ($cold | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
        $coldProcess = Start-Process -FilePath (Join-Path $installed 'FreightQuoteSystem.exe') -ArgumentList @('--mode','open-dashboard','--config',('"'+$coldConfig+'"')) -WindowStyle Hidden -PassThru
        try {
            $health = $null
            for ($attempt=0; $attempt -lt 60; $attempt++) {
                try { $health = Invoke-RestMethod -Uri ('http://127.0.0.1:'+($Port+1)+'/api/health') -TimeoutSec 2; break } catch { Start-Sleep -Milliseconds 500 }
            }
            Check 'Cold dashboard entry starts actual supervisor and collector' ($null -ne $health -and $health.connection -eq 'setup_required')
        }
        finally {
            $shutdown = Start-Process -FilePath (Join-Path $installed 'FreightQuoteSystem.exe') -ArgumentList '--mode','shutdown' -WindowStyle Hidden -PassThru -Wait
            if (-not $coldProcess.HasExited) { Stop-Process -Id $coldProcess.Id -Force }
        }
    }
    # A second installation must keep the operator configuration and existing data.
    $configFile = Join-Path $installed 'qq_live_config.json'
    $custom = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $custom.group_ids = @('10001')
    $custom.group_names = @{ '10001'='Synthetic upgrade check' }
    [IO.File]::WriteAllText($configFile, ($custom | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    $dataDir = Join-Path $installed 'data'
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $sentinel = Join-Path $dataDir 'upgrade-preservation.txt'
    [IO.File]::WriteAllText($sentinel, 'SYNTHETIC DATA MUST SURVIVE UPGRADE')
    $configHash = (Get-FileHash -LiteralPath $configFile).Hash
    $dataHash = (Get-FileHash -LiteralPath $sentinel).Hash
    $upgrade = Start-Process -FilePath $setups[0].FullName -ArgumentList $installArgs -WindowStyle Hidden -PassThru -Wait
    Check 'Upgrade exits successfully' ($upgrade.ExitCode -eq 0)
    Check 'Upgrade preserves user configuration byte-for-byte' ((Get-FileHash -LiteralPath $configFile).Hash -eq $configHash)
    Check 'Upgrade preserves existing data' ((Get-FileHash -LiteralPath $sentinel).Hash -eq $dataHash)
    if ($DisposableMachine) {
        if ($env:CI -ne 'true' -or $hadRegistry) { throw 'Uninstall test requires a clean disposable CI machine' }
        $uninstaller = Join-Path $installed 'unins000.exe'
        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -WindowStyle Hidden -PassThru -Wait
        Check 'Uninstall exits successfully' ($uninstall.ExitCode -eq 0)
        Check 'Uninstall keeps user data' ((Get-FileHash -LiteralPath $sentinel).Hash -eq $dataHash)
        Check 'Uninstall keeps user configuration' ((Get-FileHash -LiteralPath $configFile).Hash -eq $configHash)
    }
    [pscustomobject]@{installed=$installed; source_commit=$info.source_commit; version=$info.version; checks=$results; success=$true} |
        ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $root 'asset-verification.json') -Encoding UTF8
    Write-Host "RELEASE_ASSETS_OK $installed"
}
catch {
    [pscustomobject]@{installed=$installed; checks=$results; success=$false; error=$_.ToString()} |
        ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $root 'asset-verification.json') -Encoding UTF8
    throw
}
finally {
    $env:PATH = $oldPath
    $env:PYTHONPATH = $oldPythonPath
    # Restore only the registration written by THIS isolated installation.
    if (Test-Path -LiteralPath $registryKey) {
        $registration = Get-ItemProperty -LiteralPath $registryKey
        if ($registration.InstallLocation.TrimEnd('\') -eq $installed.TrimEnd('\')) {
            Remove-Item -LiteralPath $registryKey -Recurse
            if ($hadRegistry) {
                & reg.exe import $registryBackup | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'Restoring original installer registration failed' }
            }
        }
    }
}
