from configmerge import merge_configs


def test_base_only_keys_untouched():
    assert merge_configs({"a": 1, "b": 2}, {}) == {"a": 1, "b": 2}


def test_override_replaces_value():
    assert merge_configs({"a": 1}, {"a": 2}) == {"a": 2}


def test_override_adds_new_key():
    assert merge_configs({"a": 1}, {"c": 3}) == {"a": 1, "c": 3}
