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

uv venv --python "$PYTHON_312" "$VENV_ROOT/trading-ml"
uv pip install --python "$VENV_ROOT/trading-ml/bin/python" "numpy>=2,<3" "scikit-learn>=1.3,<2" "joblib>=1.3,<2"
"$VENV_ROOT/trading-ml/bin/python" -c   'import sklearn; print("scikit-learn", sklearn.__version__)'

uv venv --python "$PYTHON_312" "$VENV_ROOT/trading-finrlx"
uv pip install --python "$VENV_ROOT/trading-finrlx/bin/python" --no-deps finrl-trading
uv pip install --python "$VENV_ROOT/trading-finrlx/bin/python" numpy pandas scipy matplotlib scikit-learn bt pandas-market-calendars python-dotenv pydantic pydantic-settings sqlalchemy
mkdir -p "$HOME/vendor"
if [ ! -d "$HOME/vendor/FinRL-Trading/.git" ]; then
  git clone --depth 1 https://github.com/AI4Finance-Foundation/FinRL-Trading.git "$HOME/vendor/FinRL-Trading"
fi
(
  cd "$HOME/vendor/FinRL-Trading"
  PYTHONPATH="$HOME/vendor/FinRL-Trading" "$VENV_ROOT/trading-finrlx/bin/python" -c     'from src.backtest.backtest_engine import BacktestEngine; print("FinRL-X backtest import PASS")'
)

echo "Simulation and research worker environments are ready."
echo "export NAUTILUS_WORKER_PYTHON=$VENV_ROOT/trading-nautilus/bin/python"
echo "export LEAN_CLI=$VENV_ROOT/trading-lean/bin/lean"
echo "export ML_WORKER_PYTHON=$VENV_ROOT/trading-ml/bin/python"
echo "export FINRLX_WORKER_PYTHON=$VENV_ROOT/trading-finrlx/bin/python"
echo "export FINRLX_SOURCE=$HOME/vendor/FinRL-Trading"
