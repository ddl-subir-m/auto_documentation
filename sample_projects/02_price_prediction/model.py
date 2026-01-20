"""Model definitions for house price prediction."""

import numpy as np
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import GradientBoostingRegressor, VotingRegressor


class LinearRegressionModel:
    """Basic Linear Regression model for price prediction."""

    def __init__(self, random_state=42):
        """Initialize Linear Regression model."""
        self.random_state = random_state
        self.model = LinearRegression()

    def fit(self, X, y):
        """Train the model."""
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """Predict prices."""
        return self.model.predict(X)

    def get_feature_importance(self, feature_names):
        """Get feature importance from coefficients."""
        importance = np.abs(self.model.coef_)
        return dict(zip(feature_names, importance))


class RidgeRegressionModel:
    """Ridge Regression with L2 regularization."""

    def __init__(self, alpha=1.0, random_state=42):
        """
        Initialize Ridge Regression model.

        Args:
            alpha: Regularization strength
            random_state: Random seed
        """
        self.alpha = alpha
        self.random_state = random_state
        self.model = Ridge(alpha=alpha, random_state=random_state)

    def fit(self, X, y):
        """Train the model."""
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """Predict prices."""
        return self.model.predict(X)

    def get_feature_importance(self, feature_names):
        """Get feature importance from coefficients."""
        importance = np.abs(self.model.coef_)
        return dict(zip(feature_names, importance))


class GradientBoostingModel:
    """Gradient Boosting Regressor for price prediction."""

    def __init__(self, n_estimators=100, learning_rate=0.1, max_depth=5,
                 min_samples_split=20, min_samples_leaf=10, random_state=42):
        """
        Initialize Gradient Boosting model.

        Args:
            n_estimators: Number of boosting stages
            learning_rate: Learning rate
            max_depth: Maximum tree depth
            min_samples_split: Minimum samples to split
            min_samples_leaf: Minimum samples at leaf
            random_state: Random seed
        """
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.min_samples_leaf = min_samples_leaf
        self.random_state = random_state

        self.model = GradientBoostingRegressor(
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            max_depth=max_depth,
            min_samples_split=min_samples_split,
            min_samples_leaf=min_samples_leaf,
            random_state=random_state
        )

    def fit(self, X, y):
        """Train the model."""
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """Predict prices."""
        return self.model.predict(X)

    def get_feature_importance(self, feature_names):
        """Get feature importance."""
        importance = self.model.feature_importances_
        return dict(zip(feature_names, importance))


class EnsembleModel:
    """Ensemble of multiple models using voting."""

    def __init__(self, random_state=42):
        """
        Initialize Ensemble model.

        Combines Ridge, Gradient Boosting, and Linear models.
        """
        self.random_state = random_state

        # Create base models
        ridge = Ridge(alpha=10.0, random_state=random_state)
        gb1 = GradientBoostingRegressor(
            n_estimators=100,
            learning_rate=0.1,
            max_depth=5,
            random_state=random_state
        )
        gb2 = GradientBoostingRegressor(
            n_estimators=150,
            learning_rate=0.05,
            max_depth=4,
            random_state=random_state + 1
        )

        # Create voting ensemble
        self.model = VotingRegressor(
            estimators=[
                ('ridge', ridge),
                ('gb1', gb1),
                ('gb2', gb2)
            ]
        )

    def fit(self, X, y):
        """Train the ensemble model."""
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """Predict prices using ensemble."""
        return self.model.predict(X)

    def get_feature_importance(self, feature_names):
        """
        Get averaged feature importance from tree-based models.

        Note: Ridge doesn't have feature_importances_, so we use GB models only.
        """
        # Get importance from gradient boosting models
        gb1_importance = self.model.named_estimators_['gb1'].feature_importances_
        gb2_importance = self.model.named_estimators_['gb2'].feature_importances_

        # Average the importances
        avg_importance = (gb1_importance + gb2_importance) / 2

        return dict(zip(feature_names, avg_importance))
