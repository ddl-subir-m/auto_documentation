"""MLflow configuration module for Domino and local environments."""

import os
import sys

try:
    import mlflow
except ImportError:
    print("WARNING: MLflow is not installed. Please install it with: pip install mlflow")
    sys.exit(1)


def configure_mlflow_tracking():
    """
    Configure MLflow tracking URI based on the environment.
    
    Supports both Domino workspace (uses MLFLOW_TRACKING_URI env var)
    and local development (defaults to local server).
    
    Returns:
        str: The configured MLflow tracking URI
    """
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    
    if tracking_uri:
        print(f"Using MLflow tracking URI from environment: {tracking_uri}")
        try:
            mlflow.set_tracking_uri(tracking_uri)
        except Exception as e:
            print(f"Warning: Could not set MLflow tracking URI: {e}")
            # In case of older MLflow versions, try alternative approach
            os.environ["MLFLOW_TRACKING_URI"] = tracking_uri
    else:
        default_uri = "http://127.0.0.1:5000"
        print(f"No MLFLOW_TRACKING_URI found, using local server: {default_uri}")
        try:
            mlflow.set_tracking_uri(default_uri)
        except Exception as e:
            print(f"Warning: Could not set MLflow tracking URI: {e}")
            os.environ["MLFLOW_TRACKING_URI"] = default_uri
        tracking_uri = default_uri
    
    # Handle registry URI for model registry operations
    registry_uri = os.getenv("MLFLOW_REGISTRY_URI", tracking_uri)
    if registry_uri:
        os.environ["MLFLOW_REGISTRY_URI"] = registry_uri
    
    return tracking_uri


def is_domino_environment():
    """
    Check if running in a Domino environment.
    
    Returns:
        bool: True if running in Domino, False otherwise
    """
    domino_indicators = [
        "DOMINO_PROJECT_NAME",
        "DOMINO_PROJECT_OWNER",
        "DOMINO_RUN_ID",
        "DOMINO_WORKING_DIR",
        "DOMINO_EXTERNAL_HOST"
    ]
    
    return any(os.getenv(var) for var in domino_indicators)


def get_environment_info():
    """
    Get information about the current environment.
    
    Returns:
        dict: Environment information
    """
    info = {
        "is_domino": is_domino_environment(),
        "mlflow_tracking_uri": os.getenv("MLFLOW_TRACKING_URI"),
        "mlflow_registry_uri": os.getenv("MLFLOW_REGISTRY_URI"),
    }
    
    if info["is_domino"]:
        info["domino_project"] = os.getenv("DOMINO_PROJECT_NAME")
        info["domino_owner"] = os.getenv("DOMINO_PROJECT_OWNER")
        info["domino_run_id"] = os.getenv("DOMINO_RUN_ID")
    
    return info


def print_environment_banner():
    """Print a banner showing the current environment configuration."""
    env_info = get_environment_info()
    
    print("=" * 60)
    if env_info["is_domino"]:
        print("Running in DOMINO WORKSPACE")
        if env_info.get("domino_project"):
            print(f"Project: {env_info['domino_owner']}/{env_info['domino_project']}")
        if env_info.get("domino_run_id"):
            print(f"Run ID: {env_info['domino_run_id']}")
    else:
        print("Running in LOCAL ENVIRONMENT")
    
    print(f"MLflow Tracking URI: {env_info['mlflow_tracking_uri'] or 'http://127.0.0.1:5000 (default)'}")
    print("=" * 60)
    print()