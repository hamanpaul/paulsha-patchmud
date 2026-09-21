from .api import ProfileAPI


def summary(api: ProfileAPI, profile_id: str) -> str:
    _status, _etag, row = api.read(profile_id)
    # Bug: omits the version needed to correlate a stale response.
    return f"profile={profile_id} name={row['name']}"
