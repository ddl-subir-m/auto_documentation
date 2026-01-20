"""ML Pipeline for customer churn prediction."""

from sklearn.model_selection import train_test_split
import pandas as pd


class ChurnPipeline:
    """
    End-to-end pipeline for customer churn prediction.

    Handles data loading, feature engineering, model training, and evaluation.
    """

    def __init__(self, feature_engineer, model):
        """
        Initialize pipeline.

        Args:
            feature_engineer: Feature engineering instance
            model: Model instance
        """
        self.feature_engineer = feature_engineer
        self.model = model
        self.is_fitted = False

    def fit(self, X, y):
        """
        Fit the complete pipeline.

        Args:
            X: Training features
            y: Training target

        Returns:
            self
        """
        # Fit feature engineer
        X_transformed = self.feature_engineer.fit_transform(X)

        # Fit model
        self.model.fit(X_transformed, y)

        self.is_fitted = True
        return self

    def predict(self, X):
        """
        Make predictions using the pipeline.

        Args:
            X: Features

        Returns:
            Predicted labels
        """
        if not self.is_fitted:
            raise ValueError("Pipeline must be fitted before prediction")

        X_transformed = self.feature_engineer.transform(X)
        return self.model.predict(X_transformed)

    def predict_proba(self, X):
        """
        Predict probabilities using the pipeline.

        Args:
            X: Features

        Returns:
            Predicted probabilities
        """
        if not self.is_fitted:
            raise ValueError("Pipeline must be fitted before prediction")

        X_transformed = self.feature_engineer.transform(X)
        return self.model.predict_proba(X_transformed)

    def get_feature_names(self):
        """Get feature names after transformation."""
        return self.feature_engineer.get_feature_names()


def split_data(data, test_size=0.2, random_state=42):
    """
    Split data into train and test sets.

    Args:
        data: Full dataset
        test_size: Proportion of data for testing
        random_state: Random seed

    Returns:
        tuple: (train_data, test_data)
    """
    train_data, test_data = train_test_split(
        data,
        test_size=test_size,
        random_state=random_state,
        stratify=data['churned']
    )

    return train_data, test_data


def prepare_features_target(data, target_col='churned'):
    """
    Separate features and target from dataset.

    Args:
        data: Dataset
        target_col: Name of target column

    Returns:
        tuple: (X, y)
    """
    y = data[target_col]
    X = data.drop(columns=[target_col, 'customer_id'])

    return X, y
