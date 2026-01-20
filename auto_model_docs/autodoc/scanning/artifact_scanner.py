"""MLflow artifact scanner for extracting model metadata."""

from typing import Optional

from autodoc.core.exceptions import ScannerError
from autodoc.core.models import ArtifactContext, ModelInfo


class ArtifactScanner:
    """Scans MLflow for registered models and experiment metadata.

    This scanner queries MLflow's model registry and experiment tracking
    to extract model versions, metrics, parameters, and artifacts.
    """

    def __init__(
        self,
        tracking_uri: Optional[str] = None,
        experiment_name: Optional[str] = None,
    ):
        """Initialize the artifact scanner.

        Args:
            tracking_uri: MLflow tracking server URI.
            experiment_name: Specific experiment to query (optional).
        """
        self.tracking_uri = tracking_uri
        self.experiment_name = experiment_name
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
            # Get registered models
            models = await self._scan_registered_models(client)

            # Get experiment info if specified
            if self.experiment_name:
                project_metadata = await self._get_experiment_metadata(client)

            project_metadata["mlflow_available"] = True
            project_metadata["tracking_uri"] = self.tracking_uri

        except Exception as e:
            # Log but don't fail - MLflow might not be configured
            project_metadata["mlflow_error"] = str(e)
            project_metadata["mlflow_available"] = False

        return ArtifactContext(
            models=models,
            datasets=datasets,
            project_metadata=project_metadata,
        )

    async def _scan_registered_models(self, client) -> list[ModelInfo]:
        """Scan MLflow model registry for registered models."""
        models = []

        try:
            # Search for all registered models
            for rm in client.search_registered_models():
                # Get all versions of this model
                versions = client.search_model_versions(f"name='{rm.name}'")

                for version in versions:
                    try:
                        # Get the run associated with this version
                        run = client.get_run(version.run_id)

                        model_info = ModelInfo(
                            name=rm.name,
                            version=version.version,
                            stage=version.current_stage,
                            run_id=version.run_id,
                            metrics=dict(run.data.metrics),
                            params=dict(run.data.params),
                            artifacts=self._list_artifacts(client, version.run_id),
                        )
                        models.append(model_info)

                    except Exception:
                        # Skip versions that can't be loaded
                        models.append(ModelInfo(
                            name=rm.name,
                            version=version.version,
                            stage=version.current_stage,
                            run_id=version.run_id,
                        ))

        except Exception:
            # Model registry might not have any models
            pass

        return models

    def _list_artifacts(self, client, run_id: str) -> list[str]:
        """List artifacts for a run."""
        try:
            artifacts = client.list_artifacts(run_id)
            return [a.path for a in artifacts]
        except Exception:
            return []

    async def _get_experiment_metadata(self, client) -> dict:
        """Get experiment metadata."""
        metadata = {}

        try:
            experiment = client.get_experiment_by_name(self.experiment_name)
            if experiment:
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
