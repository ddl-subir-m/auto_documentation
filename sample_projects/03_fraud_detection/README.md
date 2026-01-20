# Fraud Detection

## Overview

This project detects fraudulent credit card transactions using machine learning. The system identifies suspicious transactions in real-time to prevent financial losses and protect customers.

## Problem Statement

Credit card fraud is a major challenge for financial institutions. This project builds models to:
- Detect fraudulent transactions with high precision and recall
- Minimize false positives to avoid customer friction
- Handle severe class imbalance (2% fraud rate)
- Provide real-time fraud risk scores

## Dataset

Synthetic credit card transaction data with realistic fraud patterns:
- **Size**: 50,000 transactions
- **Features**: 30 attributes covering transaction, merchant, user behavior, and device information
- **Target**: Binary fraud indicator (2% fraud rate - highly imbalanced)

### Key Features
- **Transaction**: Amount, merchant category, time patterns
- **Location**: Distance from home, foreign transactions
- **Behavior**: Transaction velocity, spending patterns, account history
- **Security**: Card present, PIN usage, device type
- **Risk Indicators**: Merchant trust, previous fraud history

## Model Development

### Experiment 1: Baseline
- **Experiment Name**: `fraud_detection_baseline`
- **Model**: Random Forest with balanced class weights

#### Model v1: Random Forest
- **Parameters**: n_estimators=100, max_depth=15, class_weight='balanced'
- **Performance**:
  - Precision: ~65%
  - Recall: ~58%
  - F1: ~61%
  - ROC-AUC: ~88%
  - PR-AUC: ~72%
- **Stage**: None
- **Purpose**: Establish baseline with simple balancing

### Experiment 2: Imbalance Handling
- **Experiment Name**: `fraud_detection_imbalance_handling`
- **Model**: XGBoost with SMOTE oversampling

#### Model v2: XGBoost + SMOTE
- **Parameters**: n_estimators=200, learning_rate=0.1, smote_ratio=0.5
- **Features**: Enhanced risk indicators (v2)
- **Technique**: SMOTE creates synthetic minority samples
- **Performance**:
  - Precision: ~78%
  - Recall: ~74%
  - F1: ~76%
  - ROC-AUC: ~94%
  - PR-AUC: ~84%
- **Stage**: Staging
- **Purpose**: Improved recall through synthetic oversampling

### Experiment 3: Production
- **Experiment Name**: `fraud_detection_production`
- **Model**: XGBoost with focal loss approach

#### Model v3: XGBoost + Focal Loss
- **Parameters**: n_estimators=250, learning_rate=0.05, max_depth=7
- **Features**: Comprehensive risk features (v3)
- **Technique**: Focal loss emphasizes hard-to-classify examples
- **Performance**:
  - Precision: ~82%
  - Recall: ~79%
  - F1: ~80%
  - ROC-AUC: ~96%
  - PR-AUC: ~88%
- **Stage**: Production
- **Purpose**: Best balance of precision and recall for production

## Project Structure

```
03_fraud_detection/
├── README.md
├── requirements.txt
├── train.py              # Training script with MLflow
├── model.py              # Model definitions (RF, XGBoost+SMOTE, XGBoost+FocalLoss)
├── features.py           # Feature engineering
├── pipeline.py           # ML pipeline
├── predict.py            # Inference script
└── data/
    ├── __init__.py
    └── generate_data.py  # Data generation
```

## Usage

### Setup

```bash
pip install -r requirements.txt

# Start MLflow server (from project root)
cd ../..
./sample_projects/setup_mlflow.sh
```

### Training

```bash
# Generate data and train all models
python train.py --generate-data

# Train with existing data
python train.py --data-path data/fraud_transactions.csv
```

This creates 3 experiments with 3 model versions.

### Prediction

```bash
# Predict using production model
python predict.py --data-path data/fraud_transactions.csv --stage Production

# Predict using staging model
python predict.py --data-path data/fraud_transactions.csv --stage Staging
```

## MLflow Integration

### Tracked Metrics
- precision
- recall
- f1_score
- roc_auc (ROC-AUC score)
- pr_auc (Precision-Recall AUC - critical for imbalanced data)
- specificity (True Negative Rate)
- matthews_corrcoef (MCC - balanced metric for imbalanced data)

### Logged Parameters
- model_type
- feature_engineering_version
- Model-specific hyperparameters (n_estimators, learning_rate, max_depth, smote_ratio, threshold)

### Artifacts
- confusion_matrix.png
- roc_curve.png
- precision_recall_curve.png (critical for imbalanced classification)
- feature_importance.png
- feature_importance.csv
- classification_report.txt

### Model Registry
- **Model Name**: `fraud_detector`
- **Versions**: 3 (from 3 experiments)
- **Stages**: None → Staging → Production

## Key Insights

### Top Fraud Indicators
1. **Transaction Amount**: Large transactions have higher fraud risk
2. **Foreign Transactions**: International usage is suspicious
3. **High Velocity**: Multiple transactions in short time window
4. **New Cards**: Recently issued cards are vulnerable
5. **Time Patterns**: Late night/early morning transactions
6. **Merchant Trust**: Low-trust merchants increase risk
7. **Online + Card Not Present**: Highest risk combination

### Class Imbalance Handling

#### Baseline Approach
- Class weights balance training
- Simple but limited effectiveness

#### SMOTE Approach
- Synthetic minority oversampling
- Improves recall by 16 percentage points
- Creates realistic synthetic fraud examples

#### Focal Loss Approach
- Emphasizes hard-to-classify examples
- Best overall performance
- Production-ready balance of precision/recall

### Model Comparison

| Model | Experiment | Precision | Recall | F1 | ROC-AUC | PR-AUC | Stage |
|-------|-----------|-----------|--------|-----|---------|--------|-------|
| Random Forest | baseline | 65% | 58% | 61% | 88% | 72% | None |
| XGBoost + SMOTE | imbalance_handling | 78% | 74% | 76% | 94% | 84% | Staging |
| XGBoost + Focal Loss | production | 82% | 79% | 80% | 96% | 88% | Production |

### Business Impact

**Production Model (v3) at 2% fraud rate:**
- **Precision 82%**: 82% of flagged transactions are truly fraudulent
- **Recall 79%**: Catches 79% of all fraud
- **False Positive Rate**: ~0.4% (very low customer friction)
- **Estimated Savings**: If average fraud is $500, catching 79% of 1,000 fraudulent transactions saves ~$395,000

## Evaluation Metrics Explained

### Why PR-AUC > ROC-AUC for Fraud?
- ROC-AUC can be misleading with severe imbalance
- PR-AUC focuses on minority class performance
- More representative of real-world model effectiveness

### Matthews Correlation Coefficient (MCC)
- Single metric balancing all confusion matrix components
- Ranges from -1 to +1 (higher is better)
- Robust to class imbalance

## Next Steps

1. **Real-time Deployment**: Deploy to transaction processing pipeline
2. **Monitoring**: Track model performance and drift
3. **Threshold Tuning**: Optimize decision threshold for business needs
4. **Feature Updates**: Add card BIN, merchant history features
5. **Model Updates**: Retrain weekly with new fraud patterns
6. **Explainability**: Add SHAP values for fraud reasons
