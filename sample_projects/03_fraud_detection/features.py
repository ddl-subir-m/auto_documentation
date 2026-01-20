"""Feature engineering for fraud detection."""

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler, LabelEncoder


class FraudFeatureEngineer:
    """Feature engineering pipeline for fraud detection."""

    def __init__(self, version='v1'):
        """
        Initialize feature engineer.

        Args:
            version: Feature engineering version (v1, v2, v3)
        """
        self.version = version
        self.scaler = StandardScaler()
        self.label_encoders = {}

        self.categorical_cols = ['merchant_category', 'device_type']
        self.numerical_cols = [
            'amount', 'hour_of_day', 'day_of_week', 'is_online', 'card_present',
            'pin_used', 'distance_from_home', 'is_foreign', 'merchant_trust',
            'card_age_days', 'account_age_months', 'avg_transaction_amount',
            'transactions_last_24h', 'transactions_last_week',
            'amount_to_avg_ratio', 'daily_transaction_velocity',
            'weekly_transaction_velocity', 'time_since_last_txn', 'previous_fraud_count'
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
        """Create engineered features based on version."""
        X = X.copy()

        if self.version in ['v2', 'v3']:
            X = self._add_v2_features(X)

        if self.version == 'v3':
            X = self._add_v3_features(X)

        return X

    def _add_v2_features(self, X):
        """Add v2 features: basic risk indicators."""
        # Time-based risk
        X['is_late_night'] = ((X['hour_of_day'] >= 23) | (X['hour_of_day'] <= 5)).astype(int)
        X['is_weekend'] = (X['day_of_week'] >= 5).astype(int)

        # Amount-based risk
        X['is_high_amount'] = (X['amount'] > 1000).astype(int)
        X['amount_deviation'] = np.abs(X['amount'] - X['avg_transaction_amount'])

        # Velocity risk
        X['high_velocity'] = (X['transactions_last_24h'] > 5).astype(int)

        # Location risk
        X['location_risk'] = (X['distance_from_home'] > 100).astype(int) | X['is_foreign']

        # Card/account risk
        X['new_card'] = (X['card_age_days'] < 30).astype(int)
        X['has_fraud_history'] = (X['previous_fraud_count'] > 0).astype(int)

        return X

    def _add_v3_features(self, X):
        """Add v3 features: advanced interactions."""
        # Risk score combinations
        X['online_no_card_present'] = (X['is_online'] == 1) & (X['card_present'] == 0)
        X['online_no_card_present'] = X['online_no_card_present'].astype(int)

        X['foreign_high_amount'] = (X['is_foreign'] == 1) & (X['amount'] > 500)
        X['foreign_high_amount'] = X['foreign_high_amount'].astype(int)

        # Comprehensive risk score
        X['composite_risk_score'] = (
            X['is_high_amount'] +
            X['location_risk'] +
            X['high_velocity'] +
            X['new_card'] +
            X['has_fraud_history'] +
            X['is_late_night'] +
            X['online_no_card_present']
        )

        # Trust indicators
        X['trust_score'] = X['merchant_trust'] + (X['pin_used'] * 2) + (X['card_present'] * 2)

        # Time pattern
        X['unusual_time_pattern'] = (
            (X['time_since_last_txn'] < 0.5) |  # Very fast repeat
            (X['time_since_last_txn'] > 168)     # Very long gap
        ).astype(int)

        # Account maturity
        X['account_maturity'] = np.log1p(X['account_age_months'])

        return X

    def get_feature_names(self):
        """Get feature names."""
        return self.feature_names_ if self.feature_names_ is not None else []
