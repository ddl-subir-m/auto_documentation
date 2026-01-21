#!/bin/bash
# Run all sample ML projects

set -e  # Exit on any error

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "=============================================="
echo "Running All Sample ML Projects"
echo "=============================================="
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

# Check if MLflow server is running
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

# Ensure runs are logged to the running MLflow server
export MLFLOW_TRACKING_URI="http://127.0.0.1:5000"
export MLFLOW_REGISTRY_URI="http://127.0.0.1:5000"
echo "Using MLflow tracking URI: $MLFLOW_TRACKING_URI"
echo ""

# Project 1: Customer Churn
echo "=============================================="
echo "Project 1: Customer Churn Prediction"
echo "=============================================="
cd "$SCRIPT_DIR/01_customer_churn"
echo "Installing dependencies..."
pip install -q -r requirements.txt
echo "Training models..."
python train.py --generate-data
echo ""
echo "Project 1 completed ✓"
echo ""

# Project 2: Price Prediction
echo "=============================================="
echo "Project 2: House Price Prediction"
echo "=============================================="
cd "$SCRIPT_DIR/02_price_prediction"
echo "Installing dependencies..."
pip install -q -r requirements.txt
echo "Training models..."
python train.py --generate-data
echo ""
echo "Project 2 completed ✓"
echo ""

# Project 3: Fraud Detection
echo "=============================================="
echo "Project 3: Fraud Detection"
echo "=============================================="
cd "$SCRIPT_DIR/03_fraud_detection"
echo "Installing dependencies..."
pip install -q -r requirements.txt
echo "Training models..."
python train.py --generate-data
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
echo "  - 9 experiments created"
echo "  - 10 model versions registered"
echo ""
echo "Registered Models:"
echo "  1. churn_predictor (3 versions)"
echo "  2. price_estimator (4 versions)"
echo "  3. fraud_detector (3 versions)"
echo ""
echo "View results in MLflow UI:"
echo "  http://127.0.0.1:5000"
echo ""
