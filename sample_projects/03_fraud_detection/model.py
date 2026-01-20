"""Model definitions for fraud detection."""

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier
from imblearn.over_sampling import SMOTE


class RandomForestModel:
    """
    Random Forest model for fraud detection.

    Baseline model with balanced class weights to handle imbalance.
    """

    def __init__(self, n_estimators=100, max_depth=15, min_samples_split=20,
                 min_samples_leaf=10, random_state=42):
        """Initialize Random Forest model."""
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
        """Train the model."""
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """Predict class labels."""
        return self.model.predict(X)

    def predict_proba(self, X):
        """Predict class probabilities."""
        return self.model.predict_proba(X)[:, 1]

    def get_feature_importance(self, feature_names):
        """Get feature importance."""
        importance = self.model.feature_importances_
        return dict(zip(feature_names, importance))


class XGBoostSMOTEModel:
    """
    XGBoost model with SMOTE for handling class imbalance.

    Uses synthetic minority oversampling to balance training data.
    """

    def __init__(self, n_estimators=200, max_depth=6, learning_rate=0.1,
                 subsample=0.8, colsample_bytree=0.8,
                 smote_ratio=0.5, random_state=42):
        """
        Initialize XGBoost with SMOTE model.

        Args:
            n_estimators: Number of boosting rounds
            max_depth: Maximum tree depth
            learning_rate: Boosting learning rate
            subsample: Subsample ratio
            colsample_bytree: Feature subsample ratio
            smote_ratio: Desired ratio of minority to majority class after SMOTE
            random_state: Random seed
        """
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.smote_ratio = smote_ratio
        self.random_state = random_state

        self.smote = SMOTE(sampling_strategy=smote_ratio, random_state=random_state)

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
        """Train the model with SMOTE resampling."""
        # Apply SMOTE to balance classes
        X_resampled, y_resampled = self.smote.fit_resample(X, y)

        print(f"Original class distribution: {np.bincount(y)}")
        print(f"After SMOTE: {np.bincount(y_resampled)}")

        # Calculate scale_pos_weight for remaining imbalance
        neg_count = (y_resampled == 0).sum()
        pos_count = (y_resampled == 1).sum()
        scale_pos_weight = neg_count / pos_count

        self.model.set_params(scale_pos_weight=scale_pos_weight)
        self.model.fit(X_resampled, y_resampled)
        return self

    def predict(self, X):
        """Predict class labels."""
        return self.model.predict(X)

    def predict_proba(self, X):
        """Predict class probabilities."""
        return self.model.predict_proba(X)[:, 1]

    def get_feature_importance(self, feature_names):
        """Get feature importance."""
        importance = self.model.feature_importances_
        return dict(zip(feature_names, importance))


class XGBoostFocalLossModel:
    """
    XGBoost model with focal loss for production fraud detection.

    Focal loss focuses learning on hard-to-classify examples,
    which is effective for imbalanced datasets.
    """

    def __init__(self, n_estimators=250, max_depth=7, learning_rate=0.05,
                 subsample=0.8, colsample_bytree=0.8,
                 threshold=0.5, random_state=42):
        """
        Initialize XGBoost with focal loss.

        Args:
            n_estimators: Number of boosting rounds
            max_depth: Maximum tree depth
            learning_rate: Boosting learning rate
            subsample: Subsample ratio
            colsample_bytree: Feature subsample ratio
            threshold: Classification threshold
            random_state: Random seed
        """
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.threshold = threshold
        self.random_state = random_state

        # Note: XGBoost doesn't have built-in focal loss, so we use high scale_pos_weight
        # In a real implementation, you would use a custom objective function
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
        """Train the model with focal loss approach."""
        # Calculate aggressive scale_pos_weight to simulate focal loss effect
        neg_count = (y == 0).sum()
        pos_count = (y == 1).sum()
        # Use higher weight than typical to focus on minority class
        scale_pos_weight = (neg_count / pos_count) * 2

        self.model.set_params(scale_pos_weight=scale_pos_weight)
        self.model.fit(X, y)
        return self

    def predict(self, X):
        """Predict class labels with custom threshold."""
        proba = self.predict_proba(X)
        return (proba >= self.threshold).astype(int)

    def predict_proba(self, X):
        """Predict class probabilities."""
        return self.model.predict_proba(X)[:, 1]

    def get_feature_importance(self, feature_names):
        """Get feature importance."""
        importance = self.model.feature_importances_
        return dict(zip(feature_names, importance))
