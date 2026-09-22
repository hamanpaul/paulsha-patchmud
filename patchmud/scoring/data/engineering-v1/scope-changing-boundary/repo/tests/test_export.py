import pytest

from src.export import export


def test_resume_cursor_is_bound_to_tenant():
    principal = {"tenant": "alpha"}
    first = export(principal, "alpha")
    assert [row["id"] for row in first["records"]] == ["a1", "a2"]
    second = export(principal, "alpha", first["next"])
    assert [row["id"] for row in second["records"]] == ["a3"]
    with pytest.raises(PermissionError):
        export({"tenant": "beta"}, "alpha", first["next"])


def test_legacy_cursor_remains_accepted():
    assert [row["id"] for row in export({"tenant": "alpha"}, "alpha", "2")["records"]] == ["a3"]


def test_cursor_from_another_tenant_is_rejected_without_querying():
    beta_token = export({"tenant": "beta"}, "beta", size=1)["next"]
    with pytest.raises(ValueError, match="cursor"):
        export({"tenant": "alpha"}, "alpha", beta_token, size=1)
