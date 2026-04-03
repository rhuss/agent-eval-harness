"""MLflow dataset management utilities."""

import sys
from typing import Optional


def get_or_create_dataset(name: str, experiment_name: str = ""):
    """Get an existing MLflow dataset by name, or create a new one.

    Args:
        name: Dataset name.
        experiment_name: Optional experiment to link the dataset to.

    Returns:
        MLflow EvaluationDataset, or None if MLflow unavailable.
    """
    try:
        import mlflow
        from mlflow.genai.datasets import create_dataset, search_datasets
    except ImportError:
        print("MLflow not installed. Install with: pip install 'mlflow[genai]'",
              file=sys.stderr)
        return None

    if experiment_name:
        mlflow.set_experiment(experiment_name)

    # Search for existing dataset by name
    try:
        results = search_datasets()
        for ds in results:
            if ds.name == name:
                return ds
    except Exception:
        pass

    # Create new dataset
    try:
        return create_dataset(name=name)
    except Exception as e:
        print(f"Failed to create dataset '{name}': {e}", file=sys.stderr)
        return None


def sync_records(dataset, records: list) -> int:
    """Merge records into an MLflow dataset.

    Args:
        dataset: MLflow EvaluationDataset.
        records: List of dicts with 'inputs' and optional 'expectations'.

    Returns:
        Number of records synced, or 0 on error.
    """
    if not dataset or not records:
        return 0
    try:
        dataset.merge_records(records)
        return len(records)
    except Exception as e:
        print(f"Failed to sync records: {e}", file=sys.stderr)
        return 0
