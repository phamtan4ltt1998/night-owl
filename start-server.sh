#!/usr/bin/env bash
set -euo pipefail

if [[ -d ".venv311" ]]; then
  source .venv311/bin/activate
elif [[ -d ".venv" ]]; then
  source .venv/bin/activate
else
  echo "Khong tim thay .venv311 hoac .venv. Hay tao moi truong truoc."
  exit 1
fi

python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000