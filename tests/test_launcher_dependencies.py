import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "启动物流运价系统.bat"
REQUIREMENTS = ROOT / "requirements.txt"


def launcher_environment() -> dict[str, str]:
    env = os.environ.copy()
    python_dir = str(Path(sys.executable).resolve().parent)
    env["PATH"] = python_dir + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_launcher(env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["cmd.exe", "/d", "/c", "call", str(LAUNCHER), "--check"],
        cwd=ROOT,
        env=env,
        input=b"\r\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=45,
        check=False,
    )


def main() -> None:
    launcher_text = LAUNCHER.read_text(encoding="utf-8").lower()
    requirements = REQUIREMENTS.read_text(encoding="utf-8").lower().splitlines()

    assert "import pandas, matplotlib, openpyxl, websocket, lunardate" in launcher_text
    assert any(line.strip().startswith("lunardate") for line in requirements)

    normal = run_launcher(launcher_environment())
    assert normal.returncode == 0, normal.stdout.decode("utf-8", errors="replace")
    assert b"[PASS]" in normal.stdout

    with tempfile.TemporaryDirectory(prefix="freight-missing-lunardate-") as directory:
        blocker = Path(directory) / "sitecustomize.py"
        blocker.write_text(
            "import builtins\n"
            "_real_import = builtins.__import__\n"
            "def _blocked_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
            "    if name == 'lunardate' or name.startswith('lunardate.'):\n"
            "        raise ModuleNotFoundError(\"No module named 'lunardate'\")\n"
            "    return _real_import(name, globals, locals, fromlist, level)\n"
            "builtins.__import__ = _blocked_import\n",
            encoding="utf-8",
        )
        missing_env = launcher_environment()
        existing_pythonpath = missing_env.get("PYTHONPATH", "")
        missing_env["PYTHONPATH"] = directory + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        missing = run_launcher(missing_env)

    assert missing.returncode != 0
    assert b"Required Python packages are missing" in missing.stdout

    print(json.dumps({
        "launcher_check_passes_with_dependencies": True,
        "lunardate_declared_in_requirements": True,
        "missing_lunardate_is_rejected": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
