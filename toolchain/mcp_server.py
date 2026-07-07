from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from esp_mcp_toolchain.server import serve_stdio  # noqa: E402


if __name__ == "__main__":
    serve_stdio()

