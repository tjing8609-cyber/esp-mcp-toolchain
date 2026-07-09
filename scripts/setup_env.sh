#!/usr/bin/env sh
set -eu

if ! command -v conda >/dev/null 2>&1; then
  echo "conda was not found. Install Anaconda/Miniconda or add conda to PATH." >&2
  exit 1
fi

conda env update -f environment.yml --prune
echo "Conda environment is ready: esp-mcp-toolchain"
echo "Activate it with: conda activate esp-mcp-toolchain"
