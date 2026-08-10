$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = if ($env:FREIGHT_PYTHON) { $env:FREIGHT_PYTHON } else { "python" }

$tests = @(
    "test_group_separation.py",
    "test_future_date_filter.py",
    "test_full_optimization.py",
    "test_route_management.py",
    "test_first_run.py"
)

foreach ($test in $tests) {
    & $python (Join-Path $repoRoot "tests\$test")
    if ($LASTEXITCODE -ne 0) {
        throw "Test failed: $test"
    }
}

Write-Host "ALL_TESTS_OK"
