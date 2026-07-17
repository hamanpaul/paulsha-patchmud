from configmerge import merge_configs


def test_none_override_removes_key():
    assert merge_configs({"a": 1, "b": 2}, {"b": None}) == {"a": 1}
