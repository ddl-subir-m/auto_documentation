"""Common utility functions for ML projects."""

import numpy as np
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, confusion_matrix, classification_report,
    mean_squared_error, mean_absolute_error, r2_score,
    log_loss, matthews_corrcoef, precision_recall_curve, auc
)


def calculate_classification_metrics(y_true, y_pred, y_pred_proba=None):
    """
    Calculate comprehensive classification metrics.

    Args:
        y_true: True labels
        y_pred: Predicted labels
        y_pred_proba: Predicted probabilities (optional, for ROC-AUC)

    Returns:
        dict: Dictionary of metric names and values
    """
    metrics = {
        'accuracy': accuracy_score(y_true, y_pred),
        'precision': precision_score(y_true, y_pred, average='binary', zero_division=0),
        'recall': recall_score(y_true, y_pred, average='binary', zero_division=0),
        'f1_score': f1_score(y_true, y_pred, average='binary', zero_division=0),
    }

    if y_pred_proba is not None:
        try:
            metrics['roc_auc'] = roc_auc_score(y_true, y_pred_proba)
            metrics['log_loss'] = log_loss(y_true, y_pred_proba)
        except ValueError:
            pass

    # Matthews correlation coefficient
    try:
        metrics['matthews_corrcoef'] = matthews_corrcoef(y_true, y_pred)
    except ValueError:
        pass

    return metrics


def calculate_regression_metrics(y_true, y_pred):
    """
    Calculate comprehensive regression metrics.

    Args:
        y_true: True values
        y_pred: Predicted values

    Returns:
        dict: Dictionary of metric names and values
    """
    mse = mean_squared_error(y_true, y_pred)
    mae = mean_absolute_error(y_true, y_pred)

    # Mean Absolute Percentage Error
    mape = np.mean(np.abs((y_true - y_pred) / y_true)) * 100

    metrics = {
        'rmse': np.sqrt(mse),
        'mae': mae,
        'r2_score': r2_score(y_true, y_pred),
        'mape': mape,
        'max_error': np.max(np.abs(y_true - y_pred))
    }

    return metrics


def calculate_imbalanced_metrics(y_true, y_pred, y_pred_proba=None):
    """
    Calculate metrics suitable for imbalanced classification.

    Args:
        y_true: True labels
        y_pred: Predicted labels
        y_pred_proba: Predicted probabilities

    Returns:
        dict: Dictionary of metric names and values
    """
    metrics = calculate_classification_metrics(y_true, y_pred, y_pred_proba)

    # Add precision-recall AUC for imbalanced datasets
    if y_pred_proba is not None:
        precision, recall, _ = precision_recall_curve(y_true, y_pred_proba)
        metrics['pr_auc'] = auc(recall, precision)

    # Specificity (True Negative Rate)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    metrics['specificity'] = tn / (tn + fp) if (tn + fp) > 0 else 0

    return metrics


def get_classification_report_text(y_true, y_pred, target_names=None):
    """
    Generate classification report as text.

    Args:
        y_true: True labels
        y_pred: Predicted labels
        target_names: Names of target classes

    Returns:
        str: Classification report text
    """
    return classification_report(y_true, y_pred, target_names=target_names)


def get_confusion_matrix(y_true, y_pred):
    """
    Generate confusion matrix.

    Args:
        y_true: True labels
        y_pred: Predicted labels

    Returns:
        numpy.ndarray: Confusion matrix
    """
    return confusion_matrix(y_true, y_pred)
