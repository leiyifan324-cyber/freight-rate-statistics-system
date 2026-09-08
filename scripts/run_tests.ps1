$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = if ($env:FREIGHT_PYTHON) { $env:FREIGHT_PYTHON } else { "python" }

$tests = @(
    "test_release_manifest.py",
    "test_release_onboarding.py",
    "test_text_only_reliability.py",
    "test_daily_controls.py",
    "test_group_origin_filter.py",
    "test_price_guards.py",
    "test_default_baseline.py",
    "test_manual_archive.py",
    "test_quote_format_learning.py",
    "test_data_management.py",
    "test_onebot_event_compatibility.py",
    "test_real_message_reception.py",
    "test_group_queue_pipeline.py",
    "test_message_burst_stress.py",
    "test_incremental_excel_update.py",
    "test_locked_csv_resilience.py",
    "test_dashboard_feedback.py",
    "test_launcher_dependencies.py",
    "test_release_bundle.py",
    "test_configuration_integrity.py",
    "test_configuration_recovery.py",
    "test_interface_security.py",
    "test_queue_resilience.py",
    "test_data_lifecycle.py",
    "test_cargo_rules.py",
    "test_subcategory_statistics.py",
    "test_spring_festival_large_archive.py",
    "test_supervisor_safety.py",
    "test_zero_touch_startup.py",
    "test_websocket_reconnect_integration.py",
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
