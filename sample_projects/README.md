# ML Sample Projects with MLflow Integration

## Overview

This directory contains 3 realistic machine learning sample projects designed to demonstrate the full capabilities of the auto-documentation tool. Each project follows a typical data science development lifecycle with comprehensive MLflow integration, including multiple experiments, model versioning, and production staging.

## Purpose

These sample projects serve as:
- **Test Data**: Realistic ML projects for testing the auto-documentation tool
- **Examples**: Reference implementations showing ML best practices
- **Templates**: Starting points for new ML projects
- **Documentation**: Demonstrations of proper code structure and MLflow integration

## Projects

### 1. Customer Churn Prediction (Binary Classification)
**Directory**: `01_customer_churn/`

Predicts which telecom customers will cancel their service subscriptions.

- **Dataset**: 10,000 customers, 20 features, 15% churn rate
- **Problem Type**: Binary Classification
- **Models**: Logistic Regression → Random Forest → XGBoost
- **Experiments**: 3 (baseline, feature_engineering, production)
- **Model Versions**: 3
- **Model Registry Name**: `churn_predictor`

**Key Metrics**:
- Production Model: 89% accuracy, 81% precision, 79% recall, 94% ROC-AUC

### 2. House Price Prediction (Regression)
**Directory**: `02_price_prediction/`

Predicts house prices based on property characteristics for real estate valuation.

- **Dataset**: 8,000 properties, 25 features
- **Problem Type**: Regression
- **Models**: Linear Regression → Ridge → Gradient Boosting → Ensemble
- **Experiments**: 3 (linear_models, tree_models, ensemble)
- **Model Versions**: 4
- **Model Registry Name**: `price_estimator`

**Key Metrics**:
- Production Model: $31K RMSE, $21K MAE, 0.88 R², 9% MAPE

### 3. Fraud Detection (Imbalanced Classification)
**Directory**: `03_fraud_detection/`

Detects fraudulent credit card transactions in highly imbalanced data.

- **Dataset**: 50,000 transactions, 30 features, 2% fraud rate
- **Problem Type**: Imbalanced Binary Classification
- **Models**: Random Forest → XGBoost+SMOTE → XGBoost+FocalLoss
- **Experiments**: 3 (baseline, imbalance_handling, production)
- **Model Versions**: 3
- **Model Registry Name**: `fraud_detector`

**Key Metrics**:
- Production Model: 82% precision, 79% recall, 96% ROC-AUC, 88% PR-AUC

## Directory Structure

```
sample_projects/
├── README.md                       # This file
├── setup_mlflow.sh                 # Start MLflow server
├── run_all_projects.sh             # Execute all projects
│
├── shared/                         # Common utilities
│   ├── __init__.py
│   ├── utils.py                    # Metric calculation functions
│   └── plotting.py                 # Visualization utilities
│
├── 01_customer_churn/              # Binary classification
│   ├── README.md
│   ├── requirements.txt
│   ├── train.py                    # Main training script
│   ├── model.py                    # Model definitions
│   ├── features.py                 # Feature engineering
│   ├── pipeline.py                 # ML pipeline
│   ├── predict.py                  # Inference
│   └── data/
│       └── generate_data.py        # Synthetic data generation
│
├── 02_price_prediction/            # Regression
│   ├── README.md
│   ├── requirements.txt
│   ├── train.py
│   ├── model.py
│   ├── features.py
│   ├── pipeline.py
│   ├── predict.py
│   └── data/
│       └── generate_data.py
│
└── 03_fraud_detection/             # Imbalanced classification
    ├── README.md
    ├── requirements.txt
    ├── train.py
    ├── model.py
    ├── features.py
    ├── pipeline.py
    ├── predict.py
    └── data/
        └── generate_data.py
```

## Quick Start

### Prerequisites

**System Dependencies (macOS only):**
```bash
# XGBoost requires OpenMP runtime on macOS
brew install libomp
```

**Python Dependencies:**
```bash
# Install Python dependencies
pip install numpy pandas scikit-learn xgboost mlflow matplotlib seaborn imbalanced-learn
```

### Step 1: Start MLflow Server

```bash
cd /Users/subirmansukhani/Desktop/work/auto_documentation/sample_projects
./setup_mlflow.sh
```

This starts the MLflow tracking server at http://127.0.0.1:5000

Leave this running in a terminal window.

### Step 2: Run All Projects

In a new terminal:

```bash
cd /Users/subirmansukhani/Desktop/work/auto_documentation/sample_projects
./run_all_projects.sh
```

This will:
1. Install dependencies for each project
2. Generate synthetic data
3. Train all models (10 total across 9 experiments)
4. Log everything to MLflow
5. Register models with appropriate stages

**Expected Duration**: 5-10 minutes total

### Step 3: View Results

Open http://127.0.0.1:5000 in your browser to explore:
- **Experiments**: 9 experiments across 3 projects
- **Runs**: All training runs with metrics, parameters, and artifacts
- **Models**: 3 registered models with multiple versions and stages

## Running Individual Projects

Each project can be run independently:

### Customer Churn
```bash
cd 01_customer_churn
pip install -r requirements.txt
python train.py --generate-data
```

### Price Prediction
```bash
cd 02_price_prediction
pip install -r requirements.txt
python train.py --generate-data
```

### Fraud Detection
```bash
cd 03_fraud_detection
pip install -r requirements.txt
python train.py --generate-data
```

## MLflow Integration Details

### Experiment Organization

Each project has multiple experiments representing different research phases:

**Project 1: Customer Churn**
- `customer_churn_baseline`: Initial exploration with Logistic Regression
- `customer_churn_feature_engineering`: Improved features with Random Forest
- `customer_churn_production`: Final production model with XGBoost

**Project 2: Price Prediction**
- `price_prediction_linear_models`: Linear and Ridge regression baselines
- `price_prediction_tree_models`: Gradient Boosting approach
- `price_prediction_ensemble`: Ensemble methods for production

**Project 3: Fraud Detection**
- `fraud_detection_baseline`: Random Forest with class balancing
- `fraud_detection_imbalance_handling`: XGBoost with SMOTE
- `fraud_detection_production`: XGBoost with focal loss approach

### Model Registry

Three registered models, each with multiple versions:

1. **churn_predictor** (3 versions)
   - v1: Logistic Regression (Stage: None)
   - v2: Random Forest (Stage: Staging)
   - v3: XGBoost (Stage: Production)

2. **price_estimator** (4 versions)
   - v1: Linear Regression (Stage: None)
   - v2: Ridge Regression (Stage: Staging)
   - v3: Gradient Boosting (Stage: Staging)
   - v4: Ensemble (Stage: Production)

3. **fraud_detector** (3 versions)
   - v1: Random Forest (Stage: None)
   - v2: XGBoost + SMOTE (Stage: Staging)
   - v3: XGBoost + Focal Loss (Stage: Production)

### Logged Information

Each model training run logs:

**Parameters**:
- Model type and hyperparameters
- Feature engineering version
- Training configuration

**Metrics**:
- Classification: accuracy, precision, recall, F1, ROC-AUC
- Regression: RMSE, MAE, R², MAPE
- Imbalanced: PR-AUC, specificity, Matthews correlation

**Artifacts**:
- Confusion matrices / residual plots
- ROC curves / precision-recall curves
- Feature importance plots and CSVs
- Classification reports / learning curves

## Testing Auto-Documentation

### Step 1: Configure Environment

Update `.env` file in the project root:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000
AUTODOC_CODE_ROOT=/Users/subirmansukhani/Desktop/work/auto_documentation/sample_projects/01_customer_churn
AUTODOC_OUTPUT_DIR=/Users/subirmansukhani/Desktop/work/auto_documentation/output
ANTHROPIC_API_KEY=your-api-key
```

### Step 2: Run Documentation Generator

```bash
cd /Users/subirmansukhani/Desktop/work/auto_documentation/auto_model_docs
python main.py --spec doc_spec.yaml --verbose
```

### Step 3: Verify Output

Check `output/` directory for generated Word document with:
- Model performance sections (one per model version)
- Metrics from MLflow
- Code insights from train.py, model.py, features.py
- Artifact references
- Model comparison tables

## Code Structure Standards

All projects follow these conventions:

### train.py
- Main orchestration script
- Defines experiments and model configurations
- Handles MLflow logging
- Manages model registration and staging

### model.py
- Model class definitions
- One class per model type
- Consistent interface (fit, predict, predict_proba)
- Feature importance extraction

### features.py
- Feature engineering pipeline
- Version-controlled transformations
- fit/transform interface
- Feature name tracking

### pipeline.py
- End-to-end ML pipeline
- Combines feature engineering + model
- Simplifies training and inference
- Data splitting utilities

### predict.py
- Inference script
- Loads models from registry
- Makes predictions on new data
- Generates prediction reports

### data/generate_data.py
- Synthetic data generation
- Realistic distributions and patterns
- Reproducible with random seed
- Saves to CSV

## Success Criteria

The sample projects successfully demonstrate:

- ✅ 3 complete ML projects with realistic code structure
- ✅ 9 total experiments showing ML research workflow
- ✅ 10 model versions registered in MLflow
- ✅ Proper model lifecycle (None → Staging → Production)
- ✅ Comprehensive logging (metrics, parameters, artifacts)
- ✅ Priority file structure (train.py, model.py, features.py)
- ✅ Diverse ML problem types (classification, regression, imbalanced)
- ✅ Ready for documentation generation testing

## Common Issues

### MLflow Server Not Running
```bash
# Check if server is running
curl http://127.0.0.1:5000/health

# If not, start it
./setup_mlflow.sh
```

### Port Already in Use
```bash
# Find and kill process on port 5000
lsof -ti:5000 | xargs kill -9

# Restart MLflow
./setup_mlflow.sh
```

### XGBoost Import Error (macOS)
```bash
# XGBoost requires OpenMP runtime on macOS
# Error: "Library not loaded: @rpath/libomp.dylib"
brew install libomp
```

### Import Errors
```bash
# Install missing dependencies
pip install -r 01_customer_churn/requirements.txt
pip install -r 02_price_prediction/requirements.txt
pip install -r 03_fraud_detection/requirements.txt
```

## Project Statistics

- **Total Projects**: 3
- **Total Experiments**: 9
- **Total Model Versions**: 10
- **Total Code Files**: 30+
- **Total Features**: 75+
- **Total Training Samples**: 68,000
- **Total Metrics Logged**: 100+
- **Total Artifacts**: 50+

## Next Steps

1. **Run the projects**: Execute `./run_all_projects.sh`
2. **Explore MLflow UI**: View experiments and models at http://127.0.0.1:5000
3. **Test documentation**: Run auto-documentation tool on each project
4. **Verify output**: Check generated documentation for accuracy
5. **Iterate**: Use feedback to improve auto-documentation tool

## Contributing

When adding new sample projects:
1. Follow the established code structure
2. Include comprehensive MLflow integration
3. Generate realistic synthetic data
4. Create multiple experiments showing progression
5. Register models with appropriate stages
6. Document thoroughly in README
7. Add to `run_all_projects.sh`

## License

These sample projects are part of the auto-documentation tool and follow the same license.
