"""Inference script for fraud detection."""

import argparse
import pandas as pd
import mlflow
import mlflow.sklearn


def load_model_from_registry(model_name="fraud_detector", stage="Production"):
    """Load model from MLflow Model Registry."""
    model_uri = f"models:/{model_name}/{stage}"
    print(f"Loading model from: {model_uri}")
    model = mlflow.sklearn.load_model(model_uri)
    return model


def predict_fraud(model, transaction_data):
    """Predict fraud for transactions."""
    predictions = model.predict(transaction_data)
    probabilities = model.predict_proba(transaction_data)

    results = transaction_data.copy()
    results['fraud_prediction'] = predictions
    results['fraud_probability'] = probabilities
    results['risk_level'] = pd.cut(
        probabilities,
        bins=[0, 0.3, 0.7, 1.0],
        labels=['Low', 'Medium', 'High']
    )

    return results


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description='Predict fraudulent transactions')
    parser.add_argument(
        '--data-path',
        type=str,
        required=True,
        help='Path to transaction data CSV'
    )
    parser.add_argument(
        '--model-name',
        type=str,
        default='fraud_detector',
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

    print(f"Loading data from {args.data_path}...")
    data = pd.read_csv(args.data_path)
    print(f"Loaded {len(data)} transaction records")

    model = load_model_from_registry(args.model_name, args.stage)

    print("Making predictions...")
    results = predict_fraud(model, data)

    results.to_csv(args.output_path, index=False)
    print(f"\nPredictions saved to {args.output_path}")

    print("\nPrediction Summary:")
    print(f"Total transactions: {len(results)}")
    print(f"Predicted fraudulent: {results['fraud_prediction'].sum()} ({results['fraud_prediction'].mean():.1%})")
    print("\nRisk Level Distribution:")
    print(results['risk_level'].value_counts().sort_index())
    print(f"\nAverage fraud probability: {results['fraud_probability'].mean():.2%}")

    # If ground truth is available
    if 'is_fraud' in results.columns:
        from sklearn.metrics import classification_report
        print("\nActual Performance:")
        print(classification_report(results['is_fraud'], results['fraud_prediction'],
                                   target_names=['Legitimate', 'Fraud']))


if __name__ == "__main__":
    main()
