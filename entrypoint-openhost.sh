#!/bin/sh
set -eu

DATA_DIR="${BOTTLE_APP_DATA_DIR:-/data/app_data/memray}"
mkdir -p "$DATA_DIR/reports" "$DATA_DIR/sessions"
chmod -R a+rwx "$DATA_DIR"

exec runuser -u memrayuser -- env DATA_DIR="$DATA_DIR" python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
