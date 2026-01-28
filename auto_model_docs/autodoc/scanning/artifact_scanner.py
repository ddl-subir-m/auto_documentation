"""MLflow artifact scanner for extracting model metadata."""

import asyncio
import fnmatch
import logging
import os
from typing import List, Optional, Set

from autodoc.core.exceptions import ScannerError
from autodoc.core.models import ArtifactContext, ModelInfo

logger = logging.getLogger(__name__)


class ArtifactScanner:
    """Scans MLflow for registered models and experiment metadata.

    This scanner queries MLflow's model registry and experiment tracking
    to extract model versions, metrics, parameters, and artifacts.
    """

    def __init__(
        self,
        tracking_uri: Optional[str] = None,
        experiment_name: Optional[str] = None,
        experiment_names: Optional[List[str]] = None,
        model_names: Optional[List[str]] = None,
        latest_only: bool = False,
        disable_project_filtering: bool = False,
    ):
        """Initialize the artifact scanner.

        Args:
            tracking_uri: MLflow tracking server URI.
            experiment_name: Specific experiment to query (optional, deprecated).
            experiment_names: List of experiment names to include.
            model_names: List of specific model names to include.
            latest_only: Only include the latest version of each model.
            disable_project_filtering: Disable automatic Domino project filtering.
        """
        self.tracking_uri = tracking_uri
        self.experiment_name = experiment_name  # Keep for backward compatibility
        self.experiment_names = experiment_names or []
        self.model_names = model_names or []
        self.latest_only = latest_only
        self.disable_project_filtering = disable_project_filtering
        self._client = None

    def _get_client(self):
        """Lazily initialize MLflow client."""
        if self._client is None:
            try:
                import mlflow

                if self.tracking_uri:
                    mlflow.set_tracking_uri(self.tracking_uri)
                self._client = mlflow.tracking.MlflowClient()
            except ImportError:
                return None
            except Exception:
                return None
        return self._client

    async def scan(self) -> ArtifactContext:
        """Scan MLflow for registered models and metrics.

        Returns:
            ArtifactContext with model information.
        """
        return await asyncio.to_thread(self._scan_sync)

    def _scan_sync(self) -> ArtifactContext:
        models = []
        datasets = []
        project_metadata = {}

        client = self._get_client()
        if client is None:
            # MLflow not available, return empty context
            return ArtifactContext(
                models=models,
                datasets=datasets,
                project_metadata={"mlflow_available": False},
            )

        try:
            # Get current Domino project info
            domino_project_id = None
            if not self.disable_project_filtering:
                domino_project_id = os.environ.get("DOMINO_PROJECT_ID")
                if domino_project_id:
                    logger.info(f"Filtering models to Domino project: {domino_project_id}")
                    project_metadata["domino_project_id"] = domino_project_id
                    project_metadata["domino_project_name"] = os.environ.get("DOMINO_PROJECT_NAME")

            # Get target experiments based on filtering
            target_experiments = self._get_target_experiments(client, domino_project_id)
            
            # Log filtering info
            if target_experiments:
                logger.info(f"Target experiments: {list(target_experiments.keys())}")
            if self.model_names:
                logger.info(f"Filtering to models: {self.model_names}")
            if self.latest_only:
                logger.info("Including only latest versions of each model")

            # Get registered models with filtering
            models = self._scan_registered_models(client, target_experiments)

            # Get experiment info if specified (backward compatibility)
            if self.experiment_name:
                project_metadata.update(self._get_experiment_metadata(client))

            project_metadata["mlflow_available"] = True
            project_metadata["tracking_uri"] = self.tracking_uri
            project_metadata["models_found"] = len(models)
            project_metadata["filtering_applied"] = {
                "project_filtering": not self.disable_project_filtering and domino_project_id is not None,
                "experiment_filtering": bool(self.experiment_names),
                "model_filtering": bool(self.model_names),
                "latest_only": self.latest_only,
            }

        except Exception as e:
            logger.error(f"Error scanning MLflow artifacts: {e}")
            # Log but don't fail - MLflow might not be configured
            project_metadata["mlflow_error"] = str(e)
            project_metadata["mlflow_available"] = False

        return ArtifactContext(
            models=models,
            datasets=datasets,
            project_metadata=project_metadata,
        )

    def _get_target_experiments(self, client, domino_project_id: Optional[str]) -> dict:
        """Get target experiments based on filtering criteria.
        
        Returns:
            Dict mapping experiment name to experiment ID for target experiments.
        """
        target_experiments = {}
        
        try:
            # Get all experiments
            experiments = client.search_experiments()
            
            for exp in experiments:
                # Skip deleted experiments
                if exp.lifecycle_stage == "deleted":
                    continue
                    
                # Apply Domino project filtering
                if domino_project_id and not self.disable_project_filtering:
                    project_tag = exp.tags.get("mlflow.domino.project_id")
                    if project_tag != domino_project_id:
                        continue
                
                # Apply experiment name filtering
                if self.experiment_names:
                    # Check if any pattern matches this experiment
                    matched = False
                    for pattern in self.experiment_names:
                        # Use wildcard matching if pattern contains wildcards
                        if '*' in pattern or '?' in pattern:
                            if fnmatch.fnmatch(exp.name, pattern):
                                matched = True
                                break
                        else:
                            # Exact match for non-wildcard patterns
                            if exp.name == pattern:
                                matched = True
                                break
                    
                    if not matched:
                        continue
                        
                elif self.experiment_name:  # Backward compatibility
                    if exp.name != self.experiment_name:
                        continue
                
                target_experiments[exp.name] = exp.experiment_id
                
        except Exception as e:
            logger.warning(f"Error getting target experiments: {e}")
            
        return target_experiments

    def _scan_registered_models(self, client, target_experiments: Optional[dict] = None) -> list[ModelInfo]:
        """Scan MLflow model registry for registered models."""
        models = []
        model_versions_by_name = {}  # For latest_only filtering

        try:
            # Search for all registered models
            for rm in client.search_registered_models():
                # Apply model name filtering
                if self.model_names:
                    # Check if any pattern matches this model name
                    matched = False
                    for pattern in self.model_names:
                        # Use wildcard matching if pattern contains wildcards
                        if '*' in pattern or '?' in pattern:
                            if fnmatch.fnmatch(rm.name, pattern):
                                matched = True
                                break
                        else:
                            # Exact match for non-wildcard patterns
                            if rm.name == pattern:
                                matched = True
                                break
                    
                    if not matched:
                        continue
                    
                # Get all versions of this model
                versions = client.search_model_versions(f"name='{rm.name}'")

                for version in versions:
                    try:
                        # Get the run associated with this version
                        run = client.get_run(version.run_id)
                        
                        # Check if the experiment is deleted
                        experiment = client.get_experiment(run.info.experiment_id)
                        if experiment and experiment.lifecycle_stage == "deleted":
                            # Skip models from deleted experiments
                            continue

                        # Apply experiment filtering if specified
                        if target_experiments is not None:
                            if experiment.name not in target_experiments:
                                continue

                        artifact_paths = self._list_artifacts(client, version.run_id)
                        artifact_data = self._download_and_parse_artifacts(client, version.run_id, artifact_paths)

                        model_info = ModelInfo(
                            name=rm.name,
                            version=version.version,
                            stage=version.current_stage,
                            run_id=version.run_id,
                            metrics=dict(run.data.metrics),
                            params=dict(run.data.params),
                            artifacts=artifact_paths,
                            artifact_data=artifact_data,
                        )
                        
                        # For latest_only filtering, track versions by model name
                        if self.latest_only:
                            if rm.name not in model_versions_by_name:
                                model_versions_by_name[rm.name] = []
                            model_versions_by_name[rm.name].append(model_info)
                        else:
                            models.append(model_info)

                    except Exception as e:
                        logger.debug(f"Skipping model version {rm.name} v{version.version}: {e}")
                        # Skip versions that can't be loaded - already filtered by model name patterns above

            # Apply latest_only filtering
            if self.latest_only:
                for model_name, model_list in model_versions_by_name.items():
                    # Sort by version number (descending) and take the first one
                    latest_model = max(model_list, key=lambda m: int(m.version))
                    models.append(latest_model)
                    logger.debug(f"Selected latest version of {model_name}: v{latest_model.version}")

        except Exception as e:
            logger.warning(f"Error scanning registered models: {e}")

        logger.info(f"Found {len(models)} models after filtering")
        return models

    def _list_artifacts(self, client, run_id: str) -> list[str]:
        """List artifacts for a run."""
        try:
            artifacts = client.list_artifacts(run_id)
            return [a.path for a in artifacts]
        except Exception:
            return []

    def _download_and_parse_artifacts(
        self, client, run_id: str, artifact_paths: list[str]
    ) -> dict[str, any]:
        """Download and parse CSV/text artifacts, skip images.

        Args:
            client: MLflow client instance.
            run_id: The run ID to download artifacts from.
            artifact_paths: List of artifact paths to process.

        Returns:
            Dict mapping artifact path to parsed content.
        """
        import tempfile

        import pandas as pd

        artifact_data = {}

        for path in artifact_paths:
            try:
                if path.endswith('.csv'):
                    # Download to temp directory
                    local_path = client.download_artifacts(run_id, path, tempfile.gettempdir())
                    df = pd.read_csv(local_path)
                    artifact_data[path] = df.to_dict('records')
                    os.remove(local_path)
                elif path.endswith('.txt'):
                    local_path = client.download_artifacts(run_id, path, tempfile.gettempdir())
                    with open(local_path, 'r') as f:
                        artifact_data[path] = f.read()
                    os.remove(local_path)
                # Skip images (.png, .jpg) - redundant with CSV data
            except Exception as e:
                logger.debug(f"Could not parse artifact {path}: {e}")
                continue  # Skip artifacts that can't be parsed

        return artifact_data

    def _get_experiment_metadata(self, client) -> dict:
        """Get experiment metadata."""
        metadata = {}

        try:
            experiment = client.get_experiment_by_name(self.experiment_name)
            if experiment:
                # Skip deleted experiments
                if experiment.lifecycle_stage == "deleted":
                    metadata["experiment_skipped"] = True
                    metadata["skip_reason"] = f"Experiment '{self.experiment_name}' is deleted"
                    return metadata
                    
                metadata["experiment_id"] = experiment.experiment_id
                metadata["experiment_name"] = experiment.name
                metadata["artifact_location"] = experiment.artifact_location
                metadata["lifecycle_stage"] = experiment.lifecycle_stage

                # Get run count
                runs = client.search_runs(
                    experiment_ids=[experiment.experiment_id],
                    max_results=1,
                )
                metadata["has_runs"] = len(runs) > 0

        except Exception:
            pass

        return metadata
