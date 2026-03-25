#!/bin/bash
# Domino App startup script for Auto Model Docs Studio

# Set default paths for Domino environment
export APP_HOST="0.0.0.0"
export APP_PORT="8888"

# Ensure sibling modules (domino_client, domino_job_store, spec_store) are importable
export PYTHONPATH="/mnt/code/auto_model_docs:/mnt/code:$PYTHONPATH"

# Use conda's libstdc++ so that conda-built libraries (e.g. libicui18n) find
# a CXXABI version new enough (CXXABI_1.3.15) instead of the older system copy.
export LD_LIBRARY_PATH="/opt/conda/lib:${LD_LIBRARY_PATH:-}"

# Install dependencies if requirements.txt exists
if [ -f /mnt/code/requirements.txt ]; then
    pip install -r /mnt/code/requirements.txt
fi

# Run the FastHTML web app
cd /mnt/code/auto_model_docs
python web_app_studio.py
