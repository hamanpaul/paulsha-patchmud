"""Synthetic identity compiled into the expected payments-api image."""

SERVICE = "payments-api"
RELEASE_LABEL = "prod-approved-2026-09-21"
SOURCE_COMMIT = "9d4e11c2a0f8"
IMAGE_DIGEST = "sha256:image-921"
BUILD_ID = "build-921"


def process_identity() -> dict[str, str]:
    return {
        "service": SERVICE,
        "release_label": RELEASE_LABEL,
        "source_commit": SOURCE_COMMIT,
        "image_digest": IMAGE_DIGEST,
        "build_id": BUILD_ID,
    }
