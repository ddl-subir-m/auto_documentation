#!/bin/bash
# Restart local MLflow tracking server

set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

PORT=5000

echo "Checking for MLflow server on port $PORT..."
PIDS=$(lsof -ti tcp:$PORT || true)

if [ -n "$PIDS" ]; then
    echo "Stopping existing process(es) on port $PORT: $PIDS"
    kill $PIDS
    sleep 1
else
    echo "No process found on port $PORT"
fi

echo ""
echo "Starting MLflow server..."
echo ""

exec "$SCRIPT_DIR/setup_mlflow.sh"
