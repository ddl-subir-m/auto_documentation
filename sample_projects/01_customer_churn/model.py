"""Model definitions for customer churn prediction."""

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier


class LogisticRegressionModel:
    """
    Logistic Regression model for churn prediction.

    This is the baseline model using regularized logistic regression.
    Provides good interpretability and serves as a performance benchmark.
    """

    def __init__(self, C=1.0, max_iter=1000, random_state=42):
        """
        Initialize Logistic Regression model.

        Args:
            C: Inverse of regularization strength (smaller values = stronger regularization)
            max_iter: Maximum number of iterations
            random_state: Random seed for reproducibility
        """
        self.C = C
        self.max_iter = max_iter
        self.random_state = random_state
        self.model = LogisticRegression(
            C=C,
            max_iter=max_iter,
            random_state=random_state,
            class_weight='balanced'
        )

    def fit(self, X, y):
        """
        Train the model.

        Args:
            X: Training features
            y: Training target

        Returns:
            self
        """
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """
        Predict class labels.

        Args:
            X: Features

        Returns:
            np.ndarray: Predicted labels
        """
        return self.model.predict(X)

    def predict_proba(self, X):
        """
        Predict class probabilities.

        Args:
            X: Features

        Returns:
            np.ndarray: Predicted probabilities for positive class
        """
        return self.model.predict_proba(X)[:, 1]

    def get_feature_importance(self, feature_names):
        """
        Get feature importance based on coefficient magnitudes.

        Args:
            feature_names: List of feature names

        Returns:
            dict: Feature names and importance values
        """
        importance = np.abs(self.model.coef_[0])
        return dict(zip(feature_names, importance))


class RandomForestModel:
    """
    Random Forest model for churn prediction.

    Uses ensemble of decision trees with improved feature engineering.
    Provides better performance than logistic regression with feature interactions.
    """

    def __init__(self, n_estimators=100, max_depth=10, min_samples_split=20,
                 min_samples_leaf=10, random_state=42):
        """
        Initialize Random Forest model.

        Args:
            n_estimators: Number of trees in the forest
            max_depth: Maximum depth of trees
            min_samples_split: Minimum samples required to split a node
            min_samples_leaf: Minimum samples required at leaf node
            random_state: Random seed for reproducibility
        """
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.min_samples_leaf = min_samples_leaf
        self.random_state = random_state

        self.model = RandomForestClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            min_samples_split=min_samples_split,
            min_samples_leaf=min_samples_leaf,
            random_state=random_state,
            class_weight='balanced',
            n_jobs=-1
        )

    def fit(self, X, y):
        """
        Train the model.

        Args:
            X: Training features
            y: Training target

        Returns:
            self
        """
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """
        Predict class labels.

        Args:
            X: Features

        Returns:
            np.ndarray: Predicted labels
        """
        return self.model.predict(X)

    def predict_proba(self, X):
        """
        Predict class probabilities.

        Args:
            X: Features

        Returns:
            np.ndarray: Predicted probabilities for positive class
        """
        return self.model.predict_proba(X)[:, 1]

    def get_feature_importance(self, feature_names):
        """
        Get feature importance from Random Forest.

        Args:
            feature_names: List of feature names

        Returns:
            dict: Feature names and importance values
        """
        importance = self.model.feature_importances_
        return dict(zip(feature_names, importance))


class XGBoostModel:
    """
    XGBoost model for churn prediction.

    Production-grade gradient boosting model with best performance.
    Uses advanced features and optimized hyperparameters.
    """

    def __init__(self, n_estimators=200, max_depth=6, learning_rate=0.1,
                 subsample=0.8, colsample_bytree=0.8, random_state=42):
        """
        Initialize XGBoost model.

        Args:
            n_estimators: Number of boosting rounds
            max_depth: Maximum tree depth
            learning_rate: Boosting learning rate
            subsample: Subsample ratio of training instances
            colsample_bytree: Subsample ratio of columns
            random_state: Random seed for reproducibility
        """
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.random_state = random_state

        # Calculate scale_pos_weight for imbalanced classes
        # Will be set during fit()
        self.model = XGBClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            subsample=subsample,
            colsample_bytree=colsample_bytree,
            random_state=random_state,
            eval_metric='logloss',
            n_jobs=-1
        )

    def fit(self, X, y):
        """
        Train the model.

        Args:
            X: Training features
            y: Training target

        Returns:
            self
        """
        # Calculate scale_pos_weight for class imbalance
        neg_count = (y == 0).sum()
        pos_count = (y == 1).sum()
        scale_pos_weight = neg_count / pos_count

        self.model.set_params(scale_pos_weight=scale_pos_weight)
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """
        Predict class labels.

        Args:
            X: Features

        Returns:
            np.ndarray: Predicted labels
        """
        return self.model.predict(X)

    def predict_proba(self, X):
        """
        Predict class probabilities.

        Args:
            X: Features

        Returns:
            np.ndarray: Predicted probabilities for positive class
        """
        return self.model.predict_proba(X)[:, 1]

    def get_feature_importance(self, feature_names):
        """
        Get feature importance from XGBoost.

        Args:
            feature_names: List of feature names

        Returns:
            dict: Feature names and importance values
        """
        importance = self.model.feature_importances_
        return dict(zip(feature_names, importance))


def create_model(model_type='logistic', **params):
    """
    Factory function to create model instances.

    Args:
        model_type: Type of model ('logistic', 'random_forest', 'xgboost')
        **params: Model-specific parameters

    Returns:
        Model instance
    """
    if model_type == 'logistic':
        return LogisticRegressionModel(**params)
    elif model_type == 'random_forest':
        return RandomForestModel(**params)
    elif model_type == 'xgboost':
        return XGBoostModel(**params)
    else:
        raise ValueError(f"Unknown model type: {model_type}")
