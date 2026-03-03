#!/bin/bash
# Domino App startup script for Auto Model Docs Studio

# Set default paths for Domino environment
export APP_HOST="0.0.0.0"
export APP_PORT="8888"

# Ensure sibling modules (domino_client, domino_job_store, spec_store) are importable
export PYTHONPATH="/mnt/code/auto_model_docs:/mnt/code:$PYTHONPATH"

# Install dependencies if requirements.txt exists
if [ -f /mnt/code/requirements.txt ]; then
    pip install -r /mnt/code/requirements.txt
fi

# Run the FastHTML web app
cd /mnt/code/auto_model_docs
python web_app.py
