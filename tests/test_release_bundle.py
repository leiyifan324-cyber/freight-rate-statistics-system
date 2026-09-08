import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    manifest_path = ROOT / "third_party" / "napcat-shell-windows-node.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == "v4.18.19"
    assert manifest["asset_name"] == "NapCat.Shell.Windows.Node.zip"
    assert manifest["download_url"].startswith(
        "https://github.com/NapNeko/NapCatQQ/releases/download/"
    )
    assert len(manifest["sha256"]) == 64
    assert manifest["contains_qq"] is False

    license_text = (ROOT / "third_party" / "NAPCAT_LICENSE.txt").read_text(
        encoding="utf-8"
    )
    assert "Limited Redistribution License for NapCat" in license_text
    assert "not to be used for any commercial purposes" in license_text

    config = json.loads(
        (ROOT / "config" / "qq_live_config.example.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["napcat_launcher"] == "NapCatQQ\\Start-NapCat.cmd"
    assert config["group_ids"] == [] and config["group_names"] == {}
    assert config["ocr_enabled"] is False
    assert config["data_lifecycle"]["maintenance_interval_minutes"] == 1440
    assert config["data_lifecycle"]["processed_message_retention_days"] == 1
    assert config["data_lifecycle"]["rejected_message_retention_days"] == 1
    assert config["data_lifecycle"]["dead_letter_retention_days"] == 1
    assert config["data_lifecycle"]["backup_retention_days"] == 7

    build_script = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    installer_script = (ROOT / "installer" / "assets" / "install_napcat.ps1").read_text(
        encoding="utf-8"
    )
    packaged_verifier = (ROOT / "tests" / "verify_packaged_release.ps1").read_text(
        encoding="utf-8"
    )
    installer_batch = (ROOT / "installer" / "assets" / "安装NapCatQQ.bat").read_text(
        encoding="ascii"
    )
    inno_script = (ROOT / "installer" / "FreightQuoteSystem.iss").read_text(
        encoding="utf-8"
    )
    git_attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "Get-FileHash" in build_script
    assert "The NapCatQQ archive contains QQ.exe" in build_script
    assert "Convert-BatchFilesToWindowsFormat" in build_script
    assert "[IO.File]::Move($partialNapcat, $napcatCache)" in build_script
    assert "[IO.File]::Move($partialNapcat, $napcatCache, $true)" not in build_script
    assert "Get-FileHash" in installer_script
    assert 'Filter "QQ.exe"' in installer_script
    assert 'set `"NAPCAT_DIR=%~dp0$version\\napcat`"' in installer_script
    assert 'pushd `"%NAPCAT_DIR%`"' in installer_script
    assert 'call `"launcher.bat`"' in installer_script
    assert 'call `"launcher-win10.bat`"' in installer_script
    assert "NAPCAT_LAUNCHER_CHECK_OK" in installer_script
    assert 'call `"%~dp0$version\\napcat\\launcher.bat`"' not in installer_script
    assert "NAPCAT_BATCH_CHECK_OK" in packaged_verifier
    assert 'Join-Path $appDir "安装NapCatQQ.bat"' not in packaged_verifier
    assert "install_napcat.ps1" in installer_batch
    assert "NAPCAT_BATCH_CHECK_OK" in installer_batch
    assert "install_napcat.ps1" in inno_script
    assert "*.bat text eol=crlf" in git_attributes
    assert "*.cmd text eol=crlf" in git_attributes

    print(json.dumps({
        "napcat_release_pinned": True,
        "napcat_hash_required": True,
        "qq_exclusion_required": True,
        "license_included": True,
        "windows_launcher_configured": True,
        "napcat_working_directory_guarded": True,
        "lifecycle_defaults_preserved": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
