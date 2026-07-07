from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolchain"))

from esp_mcp_toolchain.database.migrations import init_database


if __name__ == "__main__":
    init_database()

