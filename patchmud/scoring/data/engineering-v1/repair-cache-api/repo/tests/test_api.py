from src.api import ProfileAPI


def test_update_keeps_unrelated_cache_and_refreshes_target():
    api = ProfileAPI()
    api.read("a")
    api.read("b")
    api.update("a", "Ava")
    assert api.cache.items["b"]["name"] == "Bea"
    assert api.read("a")[2]["name"] == "Ava"


def test_matching_etag_is_bodyless_and_new_version_is_visible():
    api = ProfileAPI()
    status, etag, _ = api.read("a")
    assert status == 200
    assert api.read("a", etag) == (304, etag, None)
    updated = api.update("a", "Ada Lovelace")
    assert updated["version"] == 2
    assert api.read("a")[1] == "v2"
