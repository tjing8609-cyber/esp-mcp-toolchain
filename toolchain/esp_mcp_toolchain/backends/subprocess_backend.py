from __future__ import annotations

import subprocess
from pathlib import Path


def run_allowed_command(args: list[str], cwd: str | Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, timeout=timeout, text=True, capture_output=True, check=False)

