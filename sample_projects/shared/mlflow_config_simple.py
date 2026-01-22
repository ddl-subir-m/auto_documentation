"""Simple MLflow configuration using environment variables only."""

import os
import sys


def configure_mlflow_env():
    """
    Configure MLflow using environment variables only.
    This approach is more compatible across different MLflow versions.
    
    Returns:
        str: The configured MLflow tracking URI
    """
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    
    if not tracking_uri:
        # Set default for local development
        tracking_uri = "http://127.0.0.1:5000"
        os.environ["MLFLOW_TRACKING_URI"] = tracking_uri
        print(f"No MLFLOW_TRACKING_URI found, using local server: {tracking_uri}")
    else:
        print(f"Using MLflow tracking URI from environment: {tracking_uri}")
    
    # Ensure registry URI is also set
    if not os.getenv("MLFLOW_REGISTRY_URI"):
        os.environ["MLFLOW_REGISTRY_URI"] = tracking_uri
    
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
        "DOMINO_WORKING_DIR"
    ]
    
    return any(os.getenv(var) for var in domino_indicators)


def print_environment_banner():
    """Print a banner showing the current environment configuration."""
    
    is_domino = is_domino_environment()
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000 (default)")
    
    print("=" * 60)
    if is_domino:
        print("Running in DOMINO WORKSPACE")
        project = os.getenv("DOMINO_PROJECT_NAME")
        owner = os.getenv("DOMINO_PROJECT_OWNER")
        run_id = os.getenv("DOMINO_RUN_ID")
        
        if project and owner:
            print(f"Project: {owner}/{project}")
        if run_id:
            print(f"Run ID: {run_id}")
    else:
        print("Running in LOCAL ENVIRONMENT")
    
    print(f"MLflow Tracking URI: {tracking_uri}")
    print("=" * 60)
    print()