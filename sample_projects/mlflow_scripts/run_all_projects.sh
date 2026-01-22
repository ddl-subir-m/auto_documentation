#!/bin/bash
# Run all sample ML projects

set -e  # Exit on any error

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

echo "=============================================="
echo "Running All Sample ML Projects"
echo "=============================================="
echo ""

# Generate timestamp suffix for this run
# This ensures all projects in this run use the same unique suffix
EXPERIMENT_SUFFIX=$(date +"%Y%m%d_%H%M%S")
echo "Using experiment suffix: $EXPERIMENT_SUFFIX"
echo ""

# Check for system dependencies (macOS only)
if [[ "$OSTYPE" == "darwin"* ]]; then
    # Check if libomp is installed (required for XGBoost on macOS)
    if ! brew list libomp &>/dev/null; then
        echo "Checking system dependencies..."
        if ! command -v brew &> /dev/null; then
            echo "WARNING: Homebrew is not installed. XGBoost may fail without libomp."
            echo "To install Homebrew, visit: https://brew.sh"
            echo "Then run: brew install libomp"
            echo ""
        else
            echo "Installing libomp (required for XGBoost on macOS)..."
            brew install libomp
            echo "libomp installed ✓"
            echo ""
        fi
    fi
fi

# Check if we're in a Domino environment or have MLflow URI configured
if [ -n "$MLFLOW_TRACKING_URI" ]; then
    echo "Using MLflow tracking URI from environment: $MLFLOW_TRACKING_URI"
    echo ""
    # In Domino, MLflow is managed externally, no need to check server health
    if [ -n "$DOMINO_PROJECT_NAME" ] || [ -n "$DOMINO_RUN_ID" ]; then
        echo "Running in Domino workspace - using Domino's MLflow instance ✓"
        echo ""
    fi
else
    # Local environment - check if MLflow server is running
    if ! curl -s http://127.0.0.1:5000/health > /dev/null 2>&1; then
        echo "ERROR: MLflow server is not running!"
        echo "Please start the MLflow server first:"
        echo "  ./setup_mlflow.sh"
        echo ""
        echo "Run it in a separate terminal, then run this script again."
        exit 1
    fi
    
    echo "MLflow server is running ✓"
    echo ""
    
    # Set local MLflow URIs
    export MLFLOW_TRACKING_URI="http://127.0.0.1:5000"
    export MLFLOW_REGISTRY_URI="http://127.0.0.1:5000"
    echo "Using local MLflow tracking URI: $MLFLOW_TRACKING_URI"
    echo ""
fi

# Project 1: Customer Churn
echo "=============================================="
echo "Project 1: Customer Churn Prediction"
echo "=============================================="
cd "$PROJECT_ROOT/01_customer_churn"
echo "Installing dependencies..."
pip install -q -r requirements.txt
echo "Training models..."
python train.py --generate-data --experiment-suffix "$EXPERIMENT_SUFFIX"
echo ""
echo "Project 1 completed ✓"
echo ""

# Project 2: Price Prediction
echo "=============================================="
echo "Project 2: House Price Prediction"
echo "=============================================="
cd "$PROJECT_ROOT/02_price_prediction"
echo "Installing dependencies..."
pip install -q -r requirements.txt
echo "Training models..."
python train.py --generate-data --experiment-suffix "$EXPERIMENT_SUFFIX"
echo ""
echo "Project 2 completed ✓"
echo ""

# Project 3: Fraud Detection
echo "=============================================="
echo "Project 3: Fraud Detection"
echo "=============================================="
cd "$PROJECT_ROOT/03_fraud_detection"
echo "Installing dependencies..."
pip install -q -r requirements.txt
echo "Training models..."
python train.py --generate-data --experiment-suffix "$EXPERIMENT_SUFFIX"
echo ""
echo "Project 3 completed ✓"
echo ""

# Summary
echo "=============================================="
echo "ALL PROJECTS COMPLETED SUCCESSFULLY"
echo "=============================================="
echo ""
echo "Summary:"
echo "  - 3 projects executed"
echo "  - Experiments created with suffix: $EXPERIMENT_SUFFIX"
echo "    • customer_churn_$EXPERIMENT_SUFFIX"
echo "    • price_prediction_$EXPERIMENT_SUFFIX"
echo "    • fraud_detection_$EXPERIMENT_SUFFIX"
echo ""
echo "Registered Models (new versions added):"
echo "  • churn_predictor"
echo "  • price_estimator"
echo "  • fraud_detector"
echo ""
echo "View results in MLflow UI:"
if [ -n "$MLFLOW_TRACKING_URI" ] && [ "$MLFLOW_TRACKING_URI" != "http://127.0.0.1:5000" ]; then
    echo "  $MLFLOW_TRACKING_URI"
else
    echo "  http://127.0.0.1:5000"
fi
echo ""
