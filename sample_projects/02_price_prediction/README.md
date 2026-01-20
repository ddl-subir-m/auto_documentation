# House Price Prediction

## Overview

This project predicts house prices for real estate properties using machine learning regression models. The goal is to accurately estimate property values based on characteristics like location, size, condition, and amenities.

## Problem Statement

Accurate house price prediction is essential for:
- Real estate valuation and appraisal
- Buyer and seller decision-making
- Investment analysis and portfolio management
- Market trend analysis

## Dataset

Synthetic real estate data with realistic property characteristics:
- **Size**: 8,000 property records
- **Features**: 25 attributes covering location, physical characteristics, and amenities
- **Target**: House price (continuous variable)
- **Price Range**: $50,000 - $1,000,000+

### Key Features
- **Location**: Neighborhood, distance to city center
- **Size**: Square footage, lot size, bedrooms, bathrooms
- **Quality**: Property condition, quality ratings
- **Age**: Year built, renovation history
- **Amenities**: Garage, pool, fireplace, basement
- **Context**: School ratings, property type

## Model Development

### Experiment 1: Linear Models
- **Experiment Name**: `price_prediction_linear_models`
- **Models**: Linear Regression, Ridge Regression
- **Features**: Basic and engineered features (v1, v2)

#### Model v1: Linear Regression
- **Performance**:
  - RMSE: ~$45,000
  - MAE: ~$32,000
  - R²: ~0.72
  - MAPE: ~15%
- **Stage**: None
- **Purpose**: Baseline performance benchmark

#### Model v2: Ridge Regression
- **Parameters**: alpha=10.0
- **Performance**:
  - RMSE: ~$42,000
  - MAE: ~$29,000
  - R²: ~0.76
  - MAPE: ~13%
- **Stage**: Staging
- **Purpose**: Improved generalization with regularization

### Experiment 2: Tree Models
- **Experiment Name**: `price_prediction_tree_models`
- **Model**: Gradient Boosting

#### Model v3: Gradient Boosting
- **Parameters**: n_estimators=150, learning_rate=0.1, max_depth=5
- **Features**: Advanced features (v3) with log transformation
- **Performance**:
  - RMSE: ~$35,000
  - MAE: ~$24,000
  - R²: ~0.84
  - MAPE: ~10%
- **Stage**: Staging
- **Purpose**: Non-linear relationships and feature interactions

### Experiment 3: Ensemble
- **Experiment Name**: `price_prediction_ensemble`
- **Model**: Voting Ensemble (Ridge + 2× Gradient Boosting)

#### Model v4: Ensemble
- **Components**: Ridge + GradientBoosting(100, lr=0.1) + GradientBoosting(150, lr=0.05)
- **Features**: Comprehensive features (v4) with log transformation
- **Performance**:
  - RMSE: ~$31,000
  - MAE: ~$21,000
  - R²: ~0.88
  - MAPE: ~9%
- **Stage**: Production
- **Purpose**: Best performance through model diversity

## Project Structure

```
02_price_prediction/
├── README.md
├── requirements.txt
├── train.py              # Training script with MLflow
├── model.py              # Model definitions
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
python train.py --data-path data/house_prices.csv
```

This creates 3 experiments with 4 model versions.

### Prediction

```bash
# Predict using production model
python predict.py --data-path data/house_prices.csv --stage Production

# Predict using staging model
python predict.py --data-path data/house_prices.csv --stage Staging
```

## MLflow Integration

### Tracked Metrics
- rmse (Root Mean Squared Error)
- mae (Mean Absolute Error)
- r2_score (R-squared)
- mape (Mean Absolute Percentage Error)
- max_error (Maximum prediction error)

### Logged Parameters
- model_type
- feature_engineering_version
- use_log_transform
- polynomial_degree
- Model-specific hyperparameters (alpha, n_estimators, learning_rate, max_depth)

### Artifacts
- residual_plot.png
- prediction_vs_actual.png
- feature_importance.png
- feature_importance.csv

### Model Registry
- **Model Name**: `price_estimator`
- **Versions**: 4 (from 3 experiments)
- **Stages**: None → Staging → Production

## Key Insights

### Top Price Drivers
1. **Square Footage**: Primary predictor of house value
2. **Location**: Neighborhood has strong impact (Waterfront > Downtown > Suburbs)
3. **Quality**: Build quality and condition significantly affect price
4. **School Rating**: High correlation with property values
5. **Age**: Newer homes command premium (with historic exception)

### Feature Engineering Impact
- **v2 features**: +4% R² improvement (basic interactions)
- **v3 features**: +8% R² improvement (advanced interactions)
- **v4 features**: +4% R² improvement (luxury indicators, categories)
- **Log transformation**: Handles skewed price distribution effectively

### Model Comparison

| Model | Experiment | RMSE | MAE | R² | MAPE | Stage |
|-------|-----------|------|-----|-----|------|-------|
| Linear Regression | linear_models | $45K | $32K | 0.72 | 15% | None |
| Ridge Regression | linear_models | $42K | $29K | 0.76 | 13% | Staging |
| Gradient Boosting | tree_models | $35K | $24K | 0.84 | 10% | Staging |
| Ensemble | ensemble | $31K | $21K | 0.88 | 9% | Production |

## Next Steps

1. **Deployment**: Create REST API for real-time predictions
2. **Monitoring**: Track prediction accuracy on new listings
3. **Data Pipeline**: Integrate with MLS data feeds
4. **Model Updates**: Retrain quarterly with market data
5. **Explainability**: Add SHAP values for price justification
