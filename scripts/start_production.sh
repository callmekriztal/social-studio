#!/usr/bin/env bash
set -e

# ==============================================================================
# Production Startup Supervisor: Social Media Studio
# Runs database initialization, starts background scheduler worker,
# and starts Uvicorn ASGI web server with graceful termination signal handling.
# ==============================================================================

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
ENVIRONMENT="${ENVIRONMENT:-production}"

echo "========================================================"
echo " Starting Social Media Studio Production Supervisor"
echo " Environment: ${ENVIRONMENT}"
echo " Host:        ${HOST}"
echo " Port:        ${PORT}"
echo " Timestamp:   $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "========================================================"

# Step 1: Initialize Database Tables / Migrations
echo "[$(date -u +"%T")] Initializing database schema..."
python -c "from app.database import init_db; init_db()"
echo "[$(date -u +"%T")] Database schema verified and ready."

# Process tracking variables
WORKER_PID=""
UVICORN_PID=""

# Graceful termination handler
cleanup() {
    echo ""
    echo "[$(date -u +"%T")] Received termination signal. Initiating graceful shutdown..."
    
    if [ -n "$UVICORN_PID" ] && kill -0 "$UVICORN_PID" 2>/dev/null; then
        echo "[$(date -u +"%T")] Stopping Uvicorn web server (PID $UVICORN_PID)..."
        kill -TERM "$UVICORN_PID" 2>/dev/null || true
    fi
    
    if [ -n "$WORKER_PID" ] && kill -0 "$WORKER_PID" 2>/dev/null; then
        echo "[$(date -u +"%T")] Stopping Scheduler background worker (PID $WORKER_PID)..."
        kill -TERM "$WORKER_PID" 2>/dev/null || true
    fi
    
    wait "$UVICORN_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
    echo "[$(date -u +"%T")] All services stopped cleanly. Exiting."
    exit 0
}

trap cleanup SIGINT SIGTERM

# Step 2: Launch Scheduler Background Worker
echo "[$(date -u +"%T")] Starting background scheduler worker daemon..."
python -m app.scheduler.worker &
WORKER_PID=$!
echo "[$(date -u +"%T")] Scheduler worker running (PID $WORKER_PID)."

# Step 3: Launch Production ASGI Server (Uvicorn)
echo "[$(date -u +"%T")] Starting production Uvicorn ASGI server on ${HOST}:${PORT}..."
uvicorn app.main:app \
    --host "${HOST}" \
    --port "${PORT}" \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips='*' &
UVICORN_PID=$!
echo "[$(date -u +"%T")] Uvicorn ASGI server running (PID $UVICORN_PID)."

# Wait for either process to terminate
wait -n "$UVICORN_PID" "$WORKER_PID"
EXIT_CODE=$?

echo "[$(date -u +"%T")] A primary service process terminated with exit code $EXIT_CODE."
cleanup
