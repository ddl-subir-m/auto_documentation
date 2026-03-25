"""Shared fixtures for Auto Model Docs tests."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from autodoc.core.models import PYTHON_PROFILE, R_PROFILE, SAS_PROFILE, MATLAB_PROFILE
from autodoc.scanning.sanitizer import ContentSanitizer


@pytest.fixture
def tmp_code_root(tmp_path):
    """Create a temporary code root with sample ML files."""
    # Python ML file
    train_py = tmp_path / "train.py"
    train_py.write_text('''"""Training script for XGBoost model."""

import pandas as pd
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split

def load_data(path):
    """Load the dataset."""
    return pd.read_csv(path)

def train_model(X, y):
    """Train an XGBoost classifier."""
    model = XGBClassifier(n_estimators=100, max_depth=5)
    model.fit(X, y)
    return model

if __name__ == "__main__":
    df = load_data("data.csv")
    X = df.drop("target", axis=1)
    y = df["target"]
    model = train_model(X, y)
''')

    # Python utility file
    utils_py = tmp_path / "utils.py"
    utils_py.write_text('''"""Utility functions."""

def normalize(x):
    return (x - x.mean()) / x.std()
''')

    # Config file
    config_py = tmp_path / "config.py"
    config_py.write_text('''"""Configuration."""
BATCH_SIZE = 32
LEARNING_RATE = 0.01
''')

    # Test file (should be excluded)
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_train.py"
    test_file.write_text('def test_train(): pass\n')

    # Binary file
    binary_file = tmp_path / "model.pkl"
    binary_file.write_bytes(b'\x00\x01\x02\x03binary data')

    # README
    readme = tmp_path / "README.md"
    readme.write_text("# ML Project\nA sample project.\n")

    return tmp_path


@pytest.fixture
def sanitizer():
    """Create a ContentSanitizer instance."""
    return ContentSanitizer()


@pytest.fixture
def mock_llm():
    """Create a mock LLM client."""
    llm = MagicMock()
    llm.complete_json = AsyncMock()
    return llm


@pytest.fixture
def python_profile():
    return PYTHON_PROFILE


@pytest.fixture
def r_profile():
    return R_PROFILE


@pytest.fixture
def sas_profile():
    return SAS_PROFILE


@pytest.fixture
def matlab_profile():
    return MATLAB_PROFILE
