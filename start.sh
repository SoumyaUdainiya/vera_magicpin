#!/usr/bin/env bash
set -e
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port "${PORT:-8080}"
