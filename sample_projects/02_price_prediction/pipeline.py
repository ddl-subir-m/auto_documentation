"""ML Pipeline for house price prediction."""

from sklearn.model_selection import train_test_split


class PricePipeline:
    """End-to-end pipeline for house price prediction."""

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
        """Fit the complete pipeline."""
        # Transform target if needed
        y_transformed = self.feature_engineer.transform_target(y)

        # Fit feature engineer
        X_transformed = self.feature_engineer.fit_transform(X)

        # Fit model
        self.model.fit(X_transformed, y_transformed)

        self.is_fitted = True
        return self

    def predict(self, X):
        """Make predictions using the pipeline."""
        if not self.is_fitted:
            raise ValueError("Pipeline must be fitted before prediction")

        X_transformed = self.feature_engineer.transform(X)
        predictions = self.model.predict(X_transformed)

        # Inverse transform if needed
        predictions = self.feature_engineer.inverse_transform_target(predictions)

        return predictions

    def get_feature_names(self):
        """Get feature names after transformation."""
        return self.feature_engineer.get_feature_names()


def split_data(data, test_size=0.2, random_state=42):
    """Split data into train and test sets."""
    train_data, test_data = train_test_split(
        data,
        test_size=test_size,
        random_state=random_state
    )
    return train_data, test_data


def prepare_features_target(data, target_col='price'):
    """Separate features and target from dataset."""
    y = data[target_col]
    X = data.drop(columns=[target_col, 'property_id'])
    return X, y
