#!/bin/bash
# Start local MLflow tracking server (skipped in Domino environment)

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Check if we're in a Domino environment
if [ -n "$MLFLOW_TRACKING_URI" ]; then
    echo "=============================================="
    echo "Domino/External MLflow Configuration Detected"
    echo "=============================================="
    echo ""
    echo "MLflow tracking URI is already configured: $MLFLOW_TRACKING_URI"
    echo ""
    if [ -n "$DOMINO_PROJECT_NAME" ]; then
        echo "Running in Domino workspace:"
        echo "  Project: $DOMINO_PROJECT_OWNER/$DOMINO_PROJECT_NAME"
        if [ -n "$DOMINO_RUN_ID" ]; then
            echo "  Run ID: $DOMINO_RUN_ID"
        fi
        echo ""
    fi
    echo "Using the configured MLflow instance."
    echo "No local MLflow server will be started."
    echo ""
    echo "View MLflow UI at: $MLFLOW_TRACKING_URI"
    echo ""
    exit 0
fi

# Create MLflow data directory
mkdir -p "$PROJECT_ROOT/mlflow_data/mlruns"

# Determine Python command (try python first, then python3)
if command -v python &> /dev/null; then
    PYTHON_CMD="python"
elif command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
else
    echo "ERROR: Python is not found!"
    exit 1
fi

# Check if MLflow is installed
if ! $PYTHON_CMD -c "import mlflow" 2>/dev/null; then
    echo "ERROR: MLflow is not installed!"
    echo ""
    echo "Please install MLflow first:"
    echo "  pip install mlflow"
    echo ""
    echo "Or install all requirements:"
    echo "  pip install -r $PROJECT_ROOT/auto_model_docs/requirements.txt"
    exit 1
fi

# Find MLflow command (try in PATH first, then venv)
if command -v mlflow &> /dev/null; then
    MLFLOW_CMD="mlflow"
elif [ -f "$PROJECT_ROOT/venv/bin/mlflow" ]; then
    MLFLOW_CMD="$PROJECT_ROOT/venv/bin/mlflow"
else
    echo "ERROR: MLflow command not found!"
    echo "MLflow is installed but the 'mlflow' command is not available."
    echo "Please ensure your virtual environment is activated or install MLflow properly."
    exit 1
fi

echo "Starting MLflow tracking server..."
echo "Backend store: sqlite:///$PROJECT_ROOT/mlflow_data/mlflow.db"
echo "Artifact root: $PROJECT_ROOT/mlflow_data/mlruns"
echo ""
echo "MLflow UI will be available at: http://127.0.0.1:5000"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

$MLFLOW_CMD server \
    --backend-store-uri "sqlite:///$PROJECT_ROOT/mlflow_data/mlflow.db" \
    --default-artifact-root "$PROJECT_ROOT/mlflow_data/mlruns" \
    --host 127.0.0.1 \
    --port 5000
