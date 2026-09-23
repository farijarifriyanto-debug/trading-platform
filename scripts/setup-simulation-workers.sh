#!/usr/bin/env bash
set -euo pipefail

PYTHON_312="${PYTHON_312:-python3.12}"
VENV_ROOT="${TRADING_WORKER_VENV_ROOT:-$HOME/.venvs}"

command -v uv >/dev/null
command -v "$PYTHON_312" >/dev/null

mkdir -p "$VENV_ROOT"

uv venv --python "$PYTHON_312" "$VENV_ROOT/trading-nautilus"
(
  cd /tmp
  uv pip install --python "$VENV_ROOT/trading-nautilus/bin/python" --pre nautilus_trader pandas
)
"$VENV_ROOT/trading-nautilus/bin/python" -c   'import nautilus_trader; print("NautilusTrader", nautilus_trader.__version__)'

uv venv --python "$PYTHON_312" "$VENV_ROOT/trading-lean"
uv pip install --python "$VENV_ROOT/trading-lean/bin/python" lean
"$VENV_ROOT/trading-lean/bin/lean" --version

echo "Simulation worker environments are ready."
echo "export NAUTILUS_WORKER_PYTHON=$VENV_ROOT/trading-nautilus/bin/python"
echo "export LEAN_CLI=$VENV_ROOT/trading-lean/bin/lean"
