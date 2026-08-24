#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"
if [[ ! -x .venv/bin/python ]]; then
  /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e '.[jquants]'
open "http://localhost:8501"
exec .venv/bin/python -m streamlit run app.py
