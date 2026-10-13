"""The package must be typed, correctly versioned, and inert on import."""

import json
import subprocess
import sys
from pathlib import Path


def test_fresh_import_from_outside_checkout(tmp_path: Path) -> None:
    checker = Path(__file__).resolve().parents[1] / "scripts" / "check_installed.py"
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(checker)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["checks"] == "passed"
