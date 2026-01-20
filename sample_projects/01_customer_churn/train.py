"""Training script for customer churn prediction with MLflow integration."""

import sys
import os
import argparse
import pandas as pd
import mlflow
import mlflow.sklearn

# Add parent directory to path for shared utilities
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shared.utils import calculate_classification_metrics, get_classification_report_text
from shared.plotting import (
    plot_confusion_matrix, plot_roc_curve,
    plot_feature_importance
)
from features import ChurnFeatureEngineer
from model import LogisticRegressionModel, RandomForestModel, XGBoostModel
from pipeline import ChurnPipeline, split_data, prepare_features_target


# Model Registry name (shared across all experiments)
MODEL_NAME = "churn_predictor"


# Project-level experiment name for MLflow UI grouping
PROJECT_EXPERIMENT_NAME = "customer_churn"

# Define experiments representing different research phases
EXPERIMENTS = {
    "baseline": {
        "name": "customer_churn_baseline",
        "description": "Initial exploration with simple logistic regression model",
        "models": [
            {
                "class": LogisticRegressionModel,
                "name": "LogisticRegression",
                "params": {
                    "C": 1.0,
                    "max_iter": 1000,
                    "random_state": 42
                },
                "feature_version": "v1",
                "stage": "None"
            }
        ]
    },
    "feature_engineering": {
        "name": "customer_churn_feature_engineering",
        "description": "Improved feature set with Random Forest",
        "models": [
            {
                "class": RandomForestModel,
                "name": "RandomForest",
                "params": {
                    "n_estimators": 100,
                    "max_depth": 10,
                    "min_samples_split": 20,
                    "min_samples_leaf": 10,
                    "random_state": 42
                },
                "feature_version": "v2",
                "stage": "Staging"
            }
        ]
    },
    "production": {
        "name": "customer_churn_production",
        "description": "Final production model with XGBoost and advanced features",
        "models": [
            {
                "class": XGBoostModel,
                "name": "XGBoost",
                "params": {
                    "n_estimators": 200,
                    "max_depth": 6,
                    "learning_rate": 0.1,
                    "subsample": 0.8,
                    "colsample_bytree": 0.8,
                    "random_state": 42
                },
                "feature_version": "v3",
                "stage": "Production"
            }
        ]
    }
}


def train_model_in_experiment(phase_name, phase_description, model_config, train_data, test_data):
    """
    Train a single model within a specific experiment.

    Args:
        experiment_name: Name of the MLflow experiment
        model_config: Model configuration dictionary
        train_data: Training dataset
        test_data: Test dataset

    Returns:
        str: Run ID of the MLflow run
    """
    # Set project-level experiment and tag the phase for filtering
    mlflow.set_experiment(PROJECT_EXPERIMENT_NAME)

    # Prepare features and target
    X_train, y_train = prepare_features_target(train_data)
    X_test, y_test = prepare_features_target(test_data)

    # Start MLflow run
    run_name = f"{phase_name}__{model_config['name']}_run"
    with mlflow.start_run(run_name=run_name) as run:
        print(f"\n{'='*60}")
        print(f"Training {model_config['name']} in experiment: {PROJECT_EXPERIMENT_NAME}")
        print(f"MLflow Run ID: {run.info.run_id}")
        print(f"{'='*60}")
        mlflow.set_tags(
            {
                "phase_name": phase_name,
                "phase_description": phase_description,
                "project": PROJECT_EXPERIMENT_NAME,
            }
        )

        # Log parameters
        params = {
            "model_type": model_config['name'],
            "feature_engineering_version": model_config['feature_version'],
            **model_config['params']
        }
        mlflow.log_params(params)
        print(f"Logged parameters: {params}")

        # Initialize feature engineer and model
        feature_engineer = ChurnFeatureEngineer(version=model_config['feature_version'])
        model = model_config['class'](**model_config['params'])

        # Create and train pipeline
        pipeline = ChurnPipeline(feature_engineer, model)
        pipeline.fit(X_train, y_train)
        print("Model training completed")

        # Make predictions
        y_train_pred = pipeline.predict(X_train)
        y_train_proba = pipeline.predict_proba(X_train)
        y_test_pred = pipeline.predict(X_test)
        y_test_proba = pipeline.predict_proba(X_test)

        # Calculate metrics
        train_metrics = calculate_classification_metrics(y_train, y_train_pred, y_train_proba)
        test_metrics = calculate_classification_metrics(y_test, y_test_pred, y_test_proba)

        # Log training metrics
        for metric_name, value in train_metrics.items():
            mlflow.log_metric(f"train_{metric_name}", value)

        # Log test metrics
        for metric_name, value in test_metrics.items():
            mlflow.log_metric(metric_name, value)

        print(f"\nTest Metrics:")
        for metric_name, value in test_metrics.items():
            print(f"  {metric_name}: {value:.4f}")

        # Generate and log artifacts
        print("\nGenerating artifacts...")

        # Confusion matrix
        plot_confusion_matrix(
            y_test, y_test_pred,
            class_names=['Not Churned', 'Churned'],
            save_path='confusion_matrix.png'
        )
        mlflow.log_artifact('confusion_matrix.png')
        os.remove('confusion_matrix.png')

        # ROC curve
        plot_roc_curve(y_test, y_test_proba, save_path='roc_curve.png')
        mlflow.log_artifact('roc_curve.png')
        os.remove('roc_curve.png')

        # Feature importance
        feature_names = pipeline.get_feature_names()
        feature_importance = model.get_feature_importance(feature_names)

        importance_values = list(feature_importance.values())
        plot_feature_importance(
            feature_names,
            importance_values,
            top_n=20,
            save_path='feature_importance.png'
        )
        mlflow.log_artifact('feature_importance.png')
        mlflow.log_artifact('feature_importance.csv')
        os.remove('feature_importance.png')
        os.remove('feature_importance.csv')

        # Classification report
        report_text = get_classification_report_text(
            y_test, y_test_pred,
            target_names=['Not Churned', 'Churned']
        )
        with open('classification_report.txt', 'w') as f:
            f.write(report_text)
        mlflow.log_artifact('classification_report.txt')
        os.remove('classification_report.txt')

        print("Artifacts logged successfully")

        # Log model to Model Registry
        print(f"\nRegistering model to Model Registry: {MODEL_NAME}")
        mlflow.sklearn.log_model(
            pipeline,
            name="model",
            registered_model_name=MODEL_NAME
        )

        run_id = run.info.run_id

    # Assign an alias instead of deprecated stages
    stage = model_config["stage"]
    print(f"Assigning model alias for stage: {stage}")
    client = mlflow.tracking.MlflowClient()

    # Find the latest version for this model
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    if versions:
        # Get the version that was just created (associated with this run)
        latest_version = None
        for v in versions:
            if v.run_id == run_id:
                latest_version = v.version
                break

        if latest_version:
            if stage and stage != "None":
                alias = stage.lower()
                client.set_registered_model_alias(
                    name=MODEL_NAME,
                    alias=alias,
                    version=latest_version
                )
                client.set_model_version_tag(
                    name=MODEL_NAME,
                    version=latest_version,
                    key="stage",
                    value=stage
                )
                print(f"Model version {latest_version} aliased as {alias}")
            else:
                print("No model alias set for stage 'None'")

    print(f"\nCompleted training for {model_config['name']}")
    print(f"{'='*60}\n")

    return run_id


def run_all_experiments(data_path):
    """
    Run all experiments sequentially.

    Args:
        data_path: Path to the customer churn dataset
    """
    # Load data
    print("Loading data...")
    data = pd.read_csv(data_path)
    print(f"Loaded {len(data)} customer records")
    print(f"Churn rate: {data['churned'].mean():.2%}\n")

    # Split data
    train_data, test_data = split_data(data, test_size=0.2, random_state=42)
    print(f"Training set: {len(train_data)} samples")
    print(f"Test set: {len(test_data)} samples\n")

    # Run all experiments
    for exp_key, exp_config in EXPERIMENTS.items():
        print(f"\n{'#'*60}")
        print(f"# Starting Experiment: {exp_config['name']}")
        print(f"# Description: {exp_config['description']}")
        print(f"{'#'*60}")

        for model_config in exp_config['models']:
            train_model_in_experiment(
                exp_config['name'],
                exp_config['description'],
                model_config,
                train_data,
                test_data
            )

    print("\n" + "="*60)
    print("ALL EXPERIMENTS COMPLETED SUCCESSFULLY")
    print("="*60)
    print(f"\nTotal experiments run: {len(EXPERIMENTS)}")
    print(f"Total models trained: {sum(len(exp['models']) for exp in EXPERIMENTS.values())}")
    print(f"Models registered to: {MODEL_NAME}")
    print("\nView results in MLflow UI:")
    print("  http://127.0.0.1:5000")


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description='Train customer churn prediction models')
    parser.add_argument(
        '--data-path',
        type=str,
        default='data/customer_churn.csv',
        help='Path to customer churn dataset'
    )
    parser.add_argument(
        '--generate-data',
        action='store_true',
        help='Generate synthetic data before training'
    )

    args = parser.parse_args()

    # Generate data if requested
    if args.generate_data:
        print("Generating synthetic customer churn data...")
        from data.generate_data import generate_customer_data, save_data
        df = generate_customer_data(n_samples=10000, random_state=42, churn_rate=0.15)
        save_data(df, output_dir='data')
        print()

    # Check if data exists
    if not os.path.exists(args.data_path):
        print(f"Error: Data file not found at {args.data_path}")
        print("Run with --generate-data flag to create synthetic data")
        sys.exit(1)

    # Run experiments
    run_all_experiments(args.data_path)


if __name__ == "__main__":
    main()
