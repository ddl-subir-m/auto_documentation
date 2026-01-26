#!/bin/bash
# Domino App startup script for Auto Model Docs Studio

# Set default paths for Domino environment
export APP_HOST="0.0.0.0"
export APP_PORT="8888"

# Install dependencies (ensures packages are available even if not in base environment)
pip install -q -r /mnt/code/requirements.txt

# Run the FastHTML web app
python /mnt/code/auto_model_docs/web_app.py
