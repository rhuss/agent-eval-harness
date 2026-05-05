"""S3 dataset download functionality for EvalHub."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DatasetInfo:
    """Information about a downloaded dataset."""

    num_cases: int
    case_ids: list
    dest: Path


def download_dataset(s3_client, bucket: str, prefix: str, dest: Path) -> DatasetInfo:
    """Download test cases from S3 into local case directories.

    Expected S3 layout: {prefix}/{case_id}/{file}
    Example: dataset/case-001/input.yaml

    Downloads each file to: {dest}/{case_id}/{file}

    Args:
        s3_client: boto3 S3 client instance
        bucket: S3 bucket name
        prefix: S3 prefix for dataset (e.g., "dataset")
        dest: Local destination path for downloaded cases

    Returns:
        DatasetInfo with number of cases, sorted case IDs, and destination path
    """
    # Ensure prefix ends with / for proper prefix matching
    if not prefix.endswith("/"):
        prefix = f"{prefix}/"

    # Collect all objects from S3, handling pagination
    all_objects = []
    continuation_token = None

    while True:
        # Build list_objects_v2 parameters
        params = {
            "Bucket": bucket,
            "Prefix": prefix,
        }
        if continuation_token:
            params["ContinuationToken"] = continuation_token

        # List objects
        response = s3_client.list_objects_v2(**params)

        # Collect objects from this page
        if "Contents" in response:
            all_objects.extend(response["Contents"])

        # Check if there are more pages
        if response.get("IsTruncated", False):
            continuation_token = response.get("NextContinuationToken")
        else:
            break

    # Group objects by case ID, then download
    dest_resolved = dest.resolve()
    case_files: dict[str, list[tuple[str, str]]] = {}
    for obj in all_objects:
        key = obj["Key"]
        if key == prefix:
            continue
        relative_path = key[len(prefix):]
        parts = relative_path.split("/", 1)
        if len(parts) < 2:
            continue
        case_id, file_name = parts
        # Validate against path traversal (CWE-22)
        target = (dest / case_id / file_name).resolve()
        if not target.is_relative_to(dest_resolved):
            raise ValueError(f"Path traversal detected in S3 key: {key}")
        case_files.setdefault(case_id, []).append((key, file_name))

    for case_id, files in case_files.items():
        (dest / case_id).mkdir(parents=True, exist_ok=True)
        for key, file_name in files:
            s3_client.download_file(bucket, key, str(dest / case_id / file_name))

    case_ids = set(case_files.keys())

    # Sort case IDs for deterministic ordering
    sorted_case_ids = sorted(case_ids)

    return DatasetInfo(
        num_cases=len(sorted_case_ids),
        case_ids=sorted_case_ids,
        dest=dest,
    )
