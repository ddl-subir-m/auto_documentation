"""Plotting utilities for ML projects."""

import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from sklearn.metrics import confusion_matrix, roc_curve, precision_recall_curve
import pandas as pd


def plot_confusion_matrix(y_true, y_pred, class_names=None, save_path='confusion_matrix.png'):
    """
    Plot and save confusion matrix.

    Args:
        y_true: True labels
        y_pred: Predicted labels
        class_names: Names of classes
        save_path: Path to save the plot
    """
    cm = confusion_matrix(y_true, y_pred)

    plt.figure(figsize=(8, 6))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
                xticklabels=class_names or ['0', '1'],
                yticklabels=class_names or ['0', '1'])
    plt.title('Confusion Matrix')
    plt.ylabel('True Label')
    plt.xlabel('Predicted Label')
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close()


def plot_roc_curve(y_true, y_pred_proba, save_path='roc_curve.png'):
    """
    Plot and save ROC curve.

    Args:
        y_true: True labels
        y_pred_proba: Predicted probabilities
        save_path: Path to save the plot
    """
    fpr, tpr, _ = roc_curve(y_true, y_pred_proba)

    plt.figure(figsize=(8, 6))
    plt.plot(fpr, tpr, linewidth=2, label='ROC Curve')
    plt.plot([0, 1], [0, 1], 'k--', label='Random Classifier')
    plt.xlim([0.0, 1.0])
    plt.ylim([0.0, 1.05])
    plt.xlabel('False Positive Rate')
    plt.ylabel('True Positive Rate')
    plt.title('Receiver Operating Characteristic (ROC) Curve')
    plt.legend(loc='lower right')
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close()


def plot_precision_recall_curve(y_true, y_pred_proba, save_path='precision_recall_curve.png'):
    """
    Plot and save precision-recall curve.

    Args:
        y_true: True labels
        y_pred_proba: Predicted probabilities
        save_path: Path to save the plot
    """
    precision, recall, _ = precision_recall_curve(y_true, y_pred_proba)

    plt.figure(figsize=(8, 6))
    plt.plot(recall, precision, linewidth=2)
    plt.xlabel('Recall')
    plt.ylabel('Precision')
    plt.title('Precision-Recall Curve')
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close()


def plot_feature_importance(feature_names, importance_values, top_n=20, save_path='feature_importance.png'):
    """
    Plot and save feature importance.

    Args:
        feature_names: List of feature names
        importance_values: Importance values
        top_n: Number of top features to display
        save_path: Path to save the plot
    """
    # Create DataFrame and sort
    df = pd.DataFrame({
        'feature': feature_names,
        'importance': importance_values
    }).sort_values('importance', ascending=False).head(top_n)

    plt.figure(figsize=(10, 8))
    plt.barh(df['feature'], df['importance'])
    plt.xlabel('Importance')
    plt.title(f'Top {top_n} Feature Importances')
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close()

    # Also save as CSV
    csv_path = save_path.replace('.png', '.csv')
    df.to_csv(csv_path, index=False)


def plot_residuals(y_true, y_pred, save_path='residual_plot.png'):
    """
    Plot residuals for regression models.

    Args:
        y_true: True values
        y_pred: Predicted values
        save_path: Path to save the plot
    """
    residuals = y_true - y_pred

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Residual plot
    axes[0].scatter(y_pred, residuals, alpha=0.5)
    axes[0].axhline(y=0, color='r', linestyle='--')
    axes[0].set_xlabel('Predicted Values')
    axes[0].set_ylabel('Residuals')
    axes[0].set_title('Residual Plot')
    axes[0].grid(alpha=0.3)

    # Histogram of residuals
    axes[1].hist(residuals, bins=50, edgecolor='black')
    axes[1].set_xlabel('Residuals')
    axes[1].set_ylabel('Frequency')
    axes[1].set_title('Distribution of Residuals')
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close()


def plot_prediction_vs_actual(y_true, y_pred, save_path='prediction_vs_actual.png'):
    """
    Plot predicted vs actual values for regression.

    Args:
        y_true: True values
        y_pred: Predicted values
        save_path: Path to save the plot
    """
    plt.figure(figsize=(8, 8))
    plt.scatter(y_true, y_pred, alpha=0.5)

    # Perfect prediction line
    min_val = min(y_true.min(), y_pred.min())
    max_val = max(y_true.max(), y_pred.max())
    plt.plot([min_val, max_val], [min_val, max_val], 'r--', linewidth=2)

    plt.xlabel('Actual Values')
    plt.ylabel('Predicted Values')
    plt.title('Predicted vs Actual Values')
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close()


def plot_learning_curve(train_sizes, train_scores, val_scores, save_path='learning_curve.png'):
    """
    Plot learning curve.

    Args:
        train_sizes: Training set sizes
        train_scores: Training scores
        val_scores: Validation scores
        save_path: Path to save the plot
    """
    plt.figure(figsize=(10, 6))

    plt.plot(train_sizes, train_scores, 'o-', label='Training score', linewidth=2)
    plt.plot(train_sizes, val_scores, 'o-', label='Validation score', linewidth=2)

    plt.xlabel('Training Set Size')
    plt.ylabel('Score')
    plt.title('Learning Curve')
    plt.legend(loc='best')
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close()
