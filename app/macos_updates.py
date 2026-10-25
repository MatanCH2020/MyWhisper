"""Mac beta channel: validated DMG release links, never execute the Windows updater."""
import json
import re
import urllib.request
from packaging.version import Version, InvalidVersion

BASE = "https://github.com/MatanCH2020/MyWhisper"


def select_release(releases):
    candidates = []
    for release in releases:
        tag = release.get("tag_name", "")
        if release.get("draft") or not release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+b\d+", tag):
            continue
        version = tag[1:]
        name = f"MyWhisper-{version}-macos-arm64.dmg"
        expected = f"{BASE}/releases/download/{tag}/{name}"
        if any(asset.get("name") == name and asset.get("browser_download_url") == expected
               for asset in release.get("assets", [])):
            candidates.append((Version(version), version, f"{BASE}/releases/tag/{tag}"))
    return max(candidates)[1:] if candidates else (None, None)


def check():
    request = urllib.request.Request("https://api.github.com/repos/MatanCH2020/MyWhisper/releases?per_page=100",
                                     headers={"User-Agent": "MyWhisper-Mac"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return select_release(json.load(response))
