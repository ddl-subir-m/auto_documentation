"""Feature engineering for customer churn prediction."""

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler, LabelEncoder


class ChurnFeatureEngineer:
    """
    Feature engineering pipeline for customer churn prediction.

    This class handles feature transformations, creation of interaction features,
    and scaling for the churn prediction model.
    """

    def __init__(self, version='v1'):
        """
        Initialize feature engineer.

        Args:
            version: Feature engineering version (v1, v2, v3)
        """
        self.version = version
        self.scaler = StandardScaler()
        self.label_encoders = {}

        # Define feature groups
        self.categorical_cols = ['internet_service', 'contract_type', 'payment_method']
        self.numerical_cols = [
            'tenure_months', 'monthly_charges', 'total_charges',
            'senior_citizen', 'partner', 'dependents', 'phone_service',
            'multiple_lines', 'online_security', 'online_backup',
            'device_protection', 'tech_support', 'streaming_tv',
            'streaming_movies', 'paperless_billing'
        ]

        self.feature_names_ = None

    def fit(self, X, y=None):
        """
        Fit feature transformations on training data.

        Args:
            X: Input features
            y: Target (optional)

        Returns:
            self
        """
        # Fit label encoders for categorical features
        for col in self.categorical_cols:
            if col in X.columns:
                le = LabelEncoder()
                le.fit(X[col])
                self.label_encoders[col] = le

        # Create features to get the full feature set
        X_transformed = self._create_features(X)

        # Fit scaler on numerical features
        numerical_features = [col for col in X_transformed.columns if col not in self.categorical_cols]
        self.scaler.fit(X_transformed[numerical_features])

        self.feature_names_ = X_transformed.columns.tolist()

        return self

    def transform(self, X):
        """
        Transform features.

        Args:
            X: Input features

        Returns:
            pd.DataFrame: Transformed features
        """
        # Create engineered features
        X_transformed = self._create_features(X)

        # Encode categorical features
        for col in self.categorical_cols:
            if col in X_transformed.columns and col in self.label_encoders:
                X_transformed[col] = self.label_encoders[col].transform(X_transformed[col])

        # Scale numerical features
        numerical_features = [col for col in X_transformed.columns if col not in self.categorical_cols]
        X_transformed[numerical_features] = self.scaler.transform(X_transformed[numerical_features])

        return X_transformed

    def fit_transform(self, X, y=None):
        """
        Fit and transform features.

        Args:
            X: Input features
            y: Target (optional)

        Returns:
            pd.DataFrame: Transformed features
        """
        return self.fit(X, y).transform(X)

    def _create_features(self, X):
        """
        Create engineered features based on version.

        Args:
            X: Input features

        Returns:
            pd.DataFrame: Features with engineered columns
        """
        X = X.copy()

        if self.version == 'v1':
            # Basic features only
            return X

        elif self.version == 'v2':
            # Add interaction features for v2
            X = self._add_v2_features(X)
            return X

        elif self.version == 'v3':
            # Add advanced features for v3
            X = self._add_v2_features(X)
            X = self._add_v3_features(X)
            return X

        return X

    def _add_v2_features(self, X):
        """Add version 2 features (basic interactions)."""
        # Tenure categories
        X['tenure_category'] = pd.cut(
            X['tenure_months'],
            bins=[0, 12, 24, 48, 72, np.inf],
            labels=[0, 1, 2, 3, 4],
            include_lowest=True
        ).cat.add_categories([-1]).fillna(-1).astype(int)

        # Charge per tenure month
        X['charge_per_month_tenure'] = X['total_charges'] / (X['tenure_months'] + 1)

        # Service count (number of add-on services)
        service_cols = ['online_security', 'online_backup', 'device_protection',
                       'tech_support', 'streaming_tv', 'streaming_movies']
        X['service_count'] = X[service_cols].sum(axis=1)

        # High value customer indicator
        X['high_value_customer'] = (X['monthly_charges'] > X['monthly_charges'].median()).astype(int)

        return X

    def _add_v3_features(self, X):
        """Add version 3 features (advanced interactions)."""
        # Contract stability score
        contract_stability = {
            'Month-to-month': 0,
            'One year': 1,
            'Two year': 2
        }
        X['contract_stability'] = X['contract_type'].map(contract_stability)

        # Payment risk score
        payment_risk = {
            'Electronic check': 3,
            'Mailed check': 2,
            'Bank transfer': 1,
            'Credit card': 1
        }
        X['payment_risk'] = X['payment_method'].map(payment_risk)

        # Customer engagement score
        X['engagement_score'] = (
            X['service_count'] +
            (X['tenure_months'] / 12) +
            X['partner'] +
            X['dependents']
        )

        # Monthly charge category
        X['monthly_charge_category'] = pd.cut(
            X['monthly_charges'],
            bins=[0, 35, 70, 150, np.inf],
            labels=[0, 1, 2, 3],
            include_lowest=True
        ).cat.add_categories([-1]).fillna(-1).astype(int)

        # Internet user flag
        X['has_internet'] = (X['internet_service'] != 'No').astype(int)

        # Streaming services flag
        X['has_streaming'] = ((X['streaming_tv'] == 1) | (X['streaming_movies'] == 1)).astype(int)

        # Security services flag
        X['has_security_services'] = ((X['online_security'] == 1) | (X['device_protection'] == 1)).astype(int)

        return X

    def get_feature_names(self):
        """
        Get names of all features after transformation.

        Returns:
            list: Feature names
        """
        return self.feature_names_ if self.feature_names_ is not None else []


def prepare_data(data, feature_version='v1', target_col='churned'):
    """
    Prepare data for modeling.

    Args:
        data: Input DataFrame
        feature_version: Feature engineering version
        target_col: Name of target column

    Returns:
        tuple: (X, y, feature_engineer)
    """
    # Separate features and target
    y = data[target_col]
    X = data.drop(columns=[target_col, 'customer_id'])

    # Initialize and fit feature engineer
    feature_engineer = ChurnFeatureEngineer(version=feature_version)
    X_transformed = feature_engineer.fit_transform(X)

    return X_transformed, y, feature_engineer
