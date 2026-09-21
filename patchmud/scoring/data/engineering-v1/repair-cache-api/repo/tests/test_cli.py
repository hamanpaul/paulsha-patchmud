from src.api import ProfileAPI
from src.cli import summary


def test_summary_includes_identity_and_version():
    assert summary(ProfileAPI(), "a") == "profile=a name=Ada version=1"
