"""Official App dictionary mapping for the charging platform contract."""
from datetime import datetime

PLATFORM_DICTIONARY = "DCC_VEHICLE_PLATFORM_VERSION"
PLATFORM_MAX_AGE_SECONDS = 6 * 60 * 60
PLATFORM_RETRY_SECONDS = 5 * 60


def parse_platform_dictionary(response):
    """A missing or failed dictionary is never equivalent to the App default."""
    if not isinstance(response, dict) or type(response.get("code")) is not int or response["code"] != 0:
        raise ValueError("platform_dictionary_failed")
    rows = response.get("dicparamList")
    if not isinstance(rows, list):
        raise ValueError("platform_dictionary_missing")
    matches = [row for row in rows if isinstance(row, dict) and row.get("dicItemId") == PLATFORM_DICTIONARY]
    if len(matches) != 1:
        raise ValueError("platform_dictionary_ambiguous")
    values = matches[0].get("dictItemValueList")
    if not isinstance(values, list) or not values:
        raise ValueError("platform_dictionary_empty")
    result = {}
    for row in values:
        if not isinstance(row, dict):
            raise ValueError("platform_dictionary_invalid")
        project, version = row.get("dicItemValue"), row.get("dicItemValueName")
        if not isinstance(project, str) or not project or not isinstance(version, str) or version not in {"1", "2"}:
            raise ValueError("platform_dictionary_invalid")
        if project in result and result[project] != version:
            raise ValueError("platform_dictionary_conflict")
        result[project] = version
    return result


def platform_cache_fresh(verified_at, now):
    return (isinstance(verified_at, datetime) and verified_at.tzinfo is not None
            and now.tzinfo is not None
            and 0 <= (now - verified_at).total_seconds() < PLATFORM_MAX_AGE_SECONDS)


def platform_for_project(mapping, project):
    if not isinstance(project, str) or not project:
        return None
    # App VehsTMImpl.R0: a successful dictionary explicitly maps legacy projects
    # to 1; other projects, including absent keys, use platform 2.
    return "1" if mapping.get(project, "2") == "1" else "2"
