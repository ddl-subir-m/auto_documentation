"""Inference script for customer churn prediction."""

import argparse
import pandas as pd
import mlflow
import mlflow.sklearn


def load_model_from_registry(model_name="churn_predictor", stage="Production"):
    """
    Load model from MLflow Model Registry.

    Args:
        model_name: Name of the registered model
        stage: Model stage (None, Staging, Production)

    Returns:
        Loaded model
    """
    model_uri = f"models:/{model_name}/{stage}"
    print(f"Loading model from: {model_uri}")
    model = mlflow.sklearn.load_model(model_uri)
    return model


def predict_churn(model, customer_data):
    """
    Predict churn for customers.

    Args:
        model: Trained model pipeline
        customer_data: DataFrame with customer features

    Returns:
        DataFrame with predictions
    """
    # Make predictions
    predictions = model.predict(customer_data)
    probabilities = model.predict_proba(customer_data)

    # Create results DataFrame
    results = customer_data.copy()
    results['churn_prediction'] = predictions
    results['churn_probability'] = probabilities
    results['risk_level'] = pd.cut(
        probabilities,
        bins=[0, 0.3, 0.7, 1.0],
        labels=['Low', 'Medium', 'High']
    )

    return results


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description='Predict customer churn')
    parser.add_argument(
        '--data-path',
        type=str,
        required=True,
        help='Path to customer data CSV'
    )
    parser.add_argument(
        '--model-name',
        type=str,
        default='churn_predictor',
        help='Name of the registered model'
    )
    parser.add_argument(
        '--stage',
        type=str,
        default='Production',
        choices=['None', 'Staging', 'Production'],
        help='Model stage to use'
    )
    parser.add_argument(
        '--output-path',
        type=str,
        default='predictions.csv',
        help='Path to save predictions'
    )

    args = parser.parse_args()

    # Load data
    print(f"Loading data from {args.data_path}...")
    data = pd.read_csv(args.data_path)
    print(f"Loaded {len(data)} customer records")

    # Load model
    model = load_model_from_registry(args.model_name, args.stage)

    # Make predictions
    print("Making predictions...")
    results = predict_churn(model, data)

    # Save results
    results.to_csv(args.output_path, index=False)
    print(f"\nPredictions saved to {args.output_path}")

    # Print summary
    print("\nPrediction Summary:")
    print(f"Total customers: {len(results)}")
    print(f"Predicted churners: {results['churn_prediction'].sum()} ({results['churn_prediction'].mean():.1%})")
    print("\nRisk Level Distribution:")
    print(results['risk_level'].value_counts().sort_index())
    print(f"\nAverage churn probability: {results['churn_probability'].mean():.2%}")


if __name__ == "__main__":
    main()
