"""Synthetic build identity embedded in the public release fixture."""

SERVICE = "widget-api"
RELEASE_LABEL = "v4.12.0-production"
SOURCE_COMMIT = "8f31c0d9b7e4"
TREE_DIGEST = "sha256:tree-8f31c0d9"
ARTIFACT_DIGEST = "sha256:artifact-44b7"
BUILD_ID = "build-742"


def embedded_identity() -> dict[str, str]:
    """Return the identity fields that the built artifact advertises."""

    return {
        "service": SERVICE,
        "release_label": RELEASE_LABEL,
        "source_commit": SOURCE_COMMIT,
        "tree_digest": TREE_DIGEST,
        "artifact_digest": ARTIFACT_DIGEST,
        "build_id": BUILD_ID,
    }
