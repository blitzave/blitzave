import re

import httpx

# The opt-in update check, and the only network call besides SimpleFIN. It
# asks GitHub one question, "what's the latest release?", and sends nothing
# else: no version, no ID, nothing about the user's money. The comparison
# happens on the laptop. See CLAUDE.md ("Nothing leaves the machine by default").
LATEST_RELEASE_URL = "https://api.github.com/repos/blitzave/blitzave/releases/latest"

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


class UpdateCheckError(Exception):
    """GitHub couldn't say what the latest release is (offline, no release yet, ...)."""


def fetch_latest_version() -> str:
    """The latest published release's version, like "0.2.0". GitHub leaves
    drafts and pre-releases out of /releases/latest, so they never count."""
    try:
        response = httpx.get(LATEST_RELEASE_URL, timeout=5)
    except httpx.HTTPError as e:
        raise UpdateCheckError(f"could not reach GitHub: {e}") from e

    if response.status_code != 200:
        # 404 means no release is published yet, or the repo is private.
        raise UpdateCheckError(f"GitHub answered {response.status_code}")

    try:
        tag = response.json()["tag_name"]
    except (ValueError, KeyError, TypeError) as e:
        raise UpdateCheckError("GitHub's answer had no release tag") from e

    match = _VERSION.fullmatch(str(tag))
    if match is None:
        raise UpdateCheckError(f"latest release tag {tag!r} doesn't look like v1.2.3")
    return ".".join(match.groups())


def is_newer(candidate: str, installed: str) -> bool:
    """True when candidate is a later X.Y.Z than installed. Anything else
    compares as not newer, so a surprise never shows an update."""
    a = _VERSION.fullmatch(candidate)
    b = _VERSION.fullmatch(installed)
    if a is None or b is None:
        return False
    return tuple(map(int, a.groups())) > tuple(map(int, b.groups()))
