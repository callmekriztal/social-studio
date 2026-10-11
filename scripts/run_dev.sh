#!/usr/bin/env bash
set -e

# Run FastAPI backend in local development mode with hot-reloading
echo "Starting Social Media Studio backend on http://127.0.0.1:8000 ..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
