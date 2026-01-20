#!/bin/bash
# Permanently delete all MLflow experiments and models

set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

BACKEND_URI="sqlite:///$PROJECT_ROOT/mlflow_data/mlflow.db"


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

echo "MLflow backend store: $BACKEND_URI"
echo "Deleting ALL experiments..."
echo "Registered models will be deleted."
echo ""

# Delete all registered models and mark all experiments as deleted
export MLFLOW_TRACKING_URI="$BACKEND_URI"
$PYTHON_CMD - <<'PY'
from mlflow.tracking import MlflowClient
from mlflow.entities import ViewType

client = MlflowClient()

models = client.search_registered_models()
if models:
    print(f"Deleting {len(models)} registered model(s)...")
    for model in models:
        client.delete_registered_model(model.name)
        print(f"  deleted model: {model.name}")
else:
    print("No registered models found.")

experiments = client.search_experiments(view_type=ViewType.ALL)
if experiments:
    print(f"Marking {len(experiments)} experiment(s) as deleted...")
    for exp in experiments:
        client.delete_experiment(exp.experiment_id)
        print(f"  marked deleted: {exp.experiment_id}\t{exp.name}")
    print("Experiment IDs:", ",".join(exp.experiment_id for exp in experiments))
else:
    print("No experiments found.")
PY

# Permanently delete all experiments (and their runs/artifacts)
EXPERIMENT_IDS="$($PYTHON_CMD - <<'PY'
from mlflow.tracking import MlflowClient
from mlflow.entities import ViewType

client = MlflowClient()
experiments = client.search_experiments(view_type=ViewType.DELETED_ONLY)
print(",".join(exp.experiment_id for exp in experiments))
PY
)"

if [ -n "$EXPERIMENT_IDS" ]; then
    "$MLFLOW_CMD" gc \
        --backend-store-uri "$BACKEND_URI" \
        --experiment-ids "$EXPERIMENT_IDS"
else
    echo "No deleted experiments to permanently remove."
fi

echo ""
echo "Cleanup complete."
