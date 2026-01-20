"""Inference script for house price prediction."""

import argparse
import pandas as pd
import mlflow
import mlflow.sklearn


def load_model_from_registry(model_name="price_estimator", stage="Production"):
    """Load model from MLflow Model Registry."""
    model_uri = f"models:/{model_name}/{stage}"
    print(f"Loading model from: {model_uri}")
    model = mlflow.sklearn.load_model(model_uri)
    return model


def predict_prices(model, house_data):
    """Predict house prices."""
    predictions = model.predict(house_data)

    results = house_data.copy()
    results['predicted_price'] = predictions.astype(int)

    if 'price' in results.columns:
        results['price_difference'] = results['predicted_price'] - results['price']
        results['percent_error'] = (results['price_difference'] / results['price'] * 100).round(2)

    return results


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description='Predict house prices')
    parser.add_argument(
        '--data-path',
        type=str,
        required=True,
        help='Path to house data CSV'
    )
    parser.add_argument(
        '--model-name',
        type=str,
        default='price_estimator',
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
    print(f"Loaded {len(data)} property records")

    model = load_model_from_registry(args.model_name, args.stage)

    print("Making predictions...")
    results = predict_prices(model, data)

    results.to_csv(args.output_path, index=False)
    print(f"\nPredictions saved to {args.output_path}")

    print("\nPrediction Summary:")
    print(f"Mean predicted price: ${results['predicted_price'].mean():,.0f}")
    print(f"Median predicted price: ${results['predicted_price'].median():,.0f}")
    print(f"Price range: ${results['predicted_price'].min():,.0f} - ${results['predicted_price'].max():,.0f}")

    if 'price' in results.columns:
        print(f"\nMean absolute error: ${results['price_difference'].abs().mean():,.0f}")
        print(f"Mean percent error: {results['percent_error'].abs().mean():.2f}%")


if __name__ == "__main__":
    main()
