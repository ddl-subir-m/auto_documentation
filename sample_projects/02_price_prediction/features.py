"""Feature engineering for house price prediction."""

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler, LabelEncoder, PolynomialFeatures


class PriceFeatureEngineer:
    """Feature engineering pipeline for house price prediction."""

    def __init__(self, version='v1', use_log_transform=False, polynomial_degree=1):
        """
        Initialize feature engineer.

        Args:
            version: Feature engineering version (v1, v2, v3)
            use_log_transform: Apply log transform to price target
            polynomial_degree: Degree for polynomial features (1 = no polynomial)
        """
        self.version = version
        self.use_log_transform = use_log_transform
        self.polynomial_degree = polynomial_degree
        self.scaler = StandardScaler()
        self.label_encoders = {}
        self.poly = None

        if polynomial_degree > 1:
            self.poly = PolynomialFeatures(degree=polynomial_degree, include_bias=False)

        self.categorical_cols = ['neighborhood', 'property_type']
        self.numerical_cols = [
            'square_feet', 'bedrooms', 'bathrooms', 'lot_size', 'age',
            'condition', 'quality', 'has_garage', 'garage_spaces',
            'has_pool', 'has_fireplace', 'has_basement', 'is_renovated',
            'years_since_renovation', 'school_rating', 'distance_to_center'
        ]

        self.feature_names_ = None

    def fit(self, X, y=None):
        """Fit feature transformations."""
        for col in self.categorical_cols:
            if col in X.columns:
                le = LabelEncoder()
                le.fit(X[col])
                self.label_encoders[col] = le

        X_transformed = self._create_features(X)

        numerical_features = [col for col in X_transformed.columns if col not in self.categorical_cols]
        self.scaler.fit(X_transformed[numerical_features])

        self.feature_names_ = X_transformed.columns.tolist()
        return self

    def transform(self, X):
        """Transform features."""
        X_transformed = self._create_features(X)

        for col in self.categorical_cols:
            if col in X_transformed.columns and col in self.label_encoders:
                X_transformed[col] = self.label_encoders[col].transform(X_transformed[col])

        numerical_features = [col for col in X_transformed.columns if col not in self.categorical_cols]
        X_transformed[numerical_features] = self.scaler.transform(X_transformed[numerical_features])

        return X_transformed

    def fit_transform(self, X, y=None):
        """Fit and transform features."""
        return self.fit(X, y).transform(X)

    def _create_features(self, X):
        """Create engineered features."""
        X = X.copy()

        if self.version in ['v2', 'v3', 'v4']:
            X = self._add_v2_features(X)

        if self.version in ['v3', 'v4']:
            X = self._add_v3_features(X)

        if self.version == 'v4':
            X = self._add_v4_features(X)

        return X

    def _add_v2_features(self, X):
        """Add v2 features: basic interactions."""
        X['price_per_sqft_est'] = X['square_feet'] / 100  # Estimation
        X['bed_bath_ratio'] = X['bedrooms'] / (X['bathrooms'] + 0.1)
        X['total_rooms'] = X['bedrooms'] + X['bathrooms']
        X['age_category'] = pd.cut(X['age'], bins=[0, 10, 30, 60, 200], labels=[0, 1, 2, 3]).astype(int)
        X['quality_condition_score'] = X['quality'] * X['condition']
        return X

    def _add_v3_features(self, X):
        """Add v3 features: advanced interactions."""
        X['sqft_quality_interaction'] = X['square_feet'] * X['quality'] / 1000
        X['lot_to_sqft_ratio'] = X['lot_size'] / (X['square_feet'] + 1)
        X['amenity_score'] = (X['has_pool'] + X['has_fireplace'] +
                              X['has_basement'] + X['has_garage'])
        X['effective_age'] = X['age'] - (X['is_renovated'] * X['years_since_renovation'])
        X['school_distance_score'] = X['school_rating'] / (X['distance_to_center'] + 1)
        return X

    def _add_v4_features(self, X):
        """Add v4 features: ensemble-specific."""
        X['luxury_score'] = (X['quality'] >= 4).astype(int) * (X['square_feet'] > 3000).astype(int)
        X['size_category'] = pd.cut(
            X['square_feet'],
            bins=[0, 1500, 2500, 4000, 10000],
            labels=[0, 1, 2, 3]
        ).astype(int)
        X['premium_location'] = ((X['neighborhood'] == 'Waterfront') |
                                 (X['neighborhood'] == 'Downtown')).astype(int)
        return X

    def transform_target(self, y):
        """Transform target variable."""
        if self.use_log_transform:
            return np.log1p(y)
        return y

    def inverse_transform_target(self, y):
        """Inverse transform target variable."""
        if self.use_log_transform:
            return np.expm1(y)
        return y

    def get_feature_names(self):
        """Get feature names."""
        return self.feature_names_ if self.feature_names_ is not None else []
