#!/bin/bash
# Restore deleted MLflow experiments

set -e

echo "Restoring deleted experiments..."

python3 - <<'PY'
import mlflow
from mlflow.entities import ViewType

client = mlflow.tracking.MlflowClient()

# Target experiments to restore
target_experiments = ["customer_churn", "price_prediction", "fraud_detection"]

# Find and restore deleted experiments
experiments = client.search_experiments(view_type=ViewType.ALL)
restored = []

for exp in experiments:
    if exp.name in target_experiments and exp.lifecycle_stage == "deleted":
        try:
            client.restore_experiment(exp.experiment_id)
            restored.append(exp.name)
            print(f"  Restored: {exp.name} (id: {exp.experiment_id})")
        except Exception as e:
            print(f"  Failed to restore {exp.name}: {e}")

if restored:
    print(f"\nSuccessfully restored {len(restored)} experiment(s): {', '.join(restored)}")
else:
    print("\nNo experiments needed restoration.")
PY

echo ""
echo "Done! You can now run the sample projects."