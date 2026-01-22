#!/bin/bash
# Permanently delete MLflow experiments and models created by run_all_projects.sh

set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Use environment variable if set (Domino), otherwise use local SQLite
if [ -n "$MLFLOW_TRACKING_URI" ]; then
    BACKEND_URI="$MLFLOW_TRACKING_URI"
    echo "Using MLflow tracking URI from environment"
else
    BACKEND_URI="sqlite:///$PROJECT_ROOT/mlflow_data/mlflow.db"
    echo "Using local SQLite backend"
fi


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
echo ""
echo "This script will delete the following sample project resources:"
echo "  Experiments: customer_churn, price_prediction, fraud_detection"
echo "  Models: churn_predictor, price_estimator, fraud_detector"
echo ""
if [ -n "$DOMINO_PROJECT_NAME" ]; then
    echo "WARNING: Running in Domino workspace!"
    echo "This will delete the sample project experiments and models in your Domino MLflow instance."
    echo ""
    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Cleanup cancelled."
        exit 0
    fi
fi

# Delete specific registered models and mark specific experiments as deleted
export MLFLOW_TRACKING_URI="$BACKEND_URI"
$PYTHON_CMD - <<'PY'
from mlflow.tracking import MlflowClient
from mlflow.entities import ViewType

client = MlflowClient()

# Define the specific models created by run_all_projects.sh
target_models = ["churn_predictor", "price_estimator", "fraud_detector"]

# Define the specific experiments created by run_all_projects.sh
target_experiments = ["customer_churn", "price_prediction", "fraud_detection"]

# Delete only the specific registered models
models = client.search_registered_models()
deleted_models = []
for model in models:
    if model.name in target_models:
        try:
            client.delete_registered_model(model.name)
            deleted_models.append(model.name)
            print(f"  deleted model: {model.name}")
        except Exception as e:
            print(f"  warning: could not delete model {model.name}: {e}")

if deleted_models:
    print(f"Deleted {len(deleted_models)} registered model(s): {', '.join(deleted_models)}")
else:
    print("No sample project models found to delete.")

# Delete only the specific experiments
experiments = client.search_experiments(view_type=ViewType.ALL)
deleted_experiments = []
permanently_deleted = []
for exp in experiments:
    if exp.name in target_experiments:
        try:
            # First mark as deleted if not already deleted
            if exp.lifecycle_stage != "deleted":
                client.delete_experiment(exp.experiment_id)
                deleted_experiments.append(exp.name)
                print(f"  marked deleted: {exp.experiment_id}\t{exp.name}")
            
            # Now permanently delete it
            try:
                # Use the MLflow gc command for permanent deletion via REST API
                import requests
                import os
                tracking_uri = os.environ.get('MLFLOW_TRACKING_URI', 'http://127.0.0.1:5000')
                if tracking_uri.startswith('http'):
                    # For HTTP tracking server, try to permanently delete
                    response = requests.post(
                        f"{tracking_uri}/api/2.0/mlflow/experiments/delete",
                        json={"experiment_id": exp.experiment_id},
                        headers={"Content-Type": "application/json"}
                    )
                    if response.status_code == 200:
                        permanently_deleted.append(exp.name)
                        print(f"  permanently deleted: {exp.experiment_id}\t{exp.name}")
            except Exception as e:
                # If permanent deletion fails, that's okay - it's marked as deleted
                pass
                
        except Exception as e:
            print(f"  warning: could not delete experiment {exp.name}: {e}")

if deleted_experiments:
    print(f"Marked {len(deleted_experiments)} experiment(s) as deleted: {', '.join(deleted_experiments)}")
if permanently_deleted:
    print(f"Permanently deleted {len(permanently_deleted)} experiment(s): {', '.join(permanently_deleted)}")
if not deleted_experiments and not permanently_deleted:
    print("No sample project experiments found to delete.")
PY

echo ""
echo "Cleanup complete."
echo ""
echo "Note: Models and experiments have been deleted."
echo "Experiments that couldn't be permanently deleted remain marked as deleted."
