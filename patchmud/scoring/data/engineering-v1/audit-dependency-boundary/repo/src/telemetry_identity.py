"""Synthetic dependency contract emitted by the tested build."""

SERVICE = "event-gateway"
BUILD_SOURCE_COMMIT = "a1170f4c2a11"
DECLARED_DEPENDENCY = "telemetry-lib==3.7.2"
SECURITY_LABEL = "patched"


def startup_identity() -> dict[str, str]:
    return {
        "service": SERVICE,
        "build_source_commit": BUILD_SOURCE_COMMIT,
        "declared_dependency": DECLARED_DEPENDENCY,
        "security_label": SECURITY_LABEL,
    }
