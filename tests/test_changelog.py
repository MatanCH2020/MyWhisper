"""Keeps CHANGELOG.md, app/version.py and the git tags in agreement.

The in-app "מה חדש" screen renders CHANGELOG.md and highlights the entry
matching `__version__`. Nothing enforced that the file was updated when a
version was released, and six patch versions (1.5.1-1.5.3, 1.7.1, 1.8.1,
1.8.2) had already drifted out of it before these tests existed — users on
those builds saw no entry highlighted at all.

The git-tag comparison is skipped when git isn't available (e.g. a release
tarball), so this stays a pure unit test everywhere else.
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from version import __version__

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

# Versions summarised under one "גרסאות קודמות (1.0 - 1.4)" heading rather than
# given their own section — a deliberate editorial choice, not drift.
SUMMARISED = {
    "1.0.0", "1.1.0", "1.2.0", "1.2.1", "1.3.0", "1.4.0", "1.4.1",
}


def _changelog_versions():
    """Versions with their own '## גרסה X.Y.Z' section, newest first."""
    out = []
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("## "):
            m = re.search(r"(\d+\.\d+\.\d+)", s)
            if m:
                out.append(m.group(1))
    return out


def _git_tags():
    try:
        r = subprocess.run(["git", "tag", "--list"], cwd=ROOT,
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [t.lstrip("v") for t in r.stdout.split()
            if re.fullmatch(r"v?\d+\.\d+\.\d+", t)]


def _key(v):
    return [int(x) for x in v.split(".")]


class ChangelogTestCase(unittest.TestCase):
    def test_current_version_has_an_entry(self):
        """The installed version must be in the changelog, or the 'what's new'
        screen highlights nothing. This is the check that catches a forgotten
        CHANGELOG edit at release time."""
        self.assertIn(
            __version__, _changelog_versions(),
            f"app/version.py is {__version__} but CHANGELOG.md has no "
            f"'## גרסה {__version__}' section — add one before releasing.")

    def test_current_version_is_the_newest_entry(self):
        versions = _changelog_versions()
        self.assertEqual(
            versions[0], __version__,
            f"newest changelog entry is {versions[0]}, expected {__version__}")

    def test_entries_are_in_descending_order(self):
        versions = _changelog_versions()
        self.assertEqual(versions, sorted(versions, key=_key, reverse=True),
                         "changelog sections must run newest-first")

    def test_no_duplicate_entries(self):
        versions = _changelog_versions()
        dupes = {v for v in versions if versions.count(v) > 1}
        self.assertFalse(dupes, f"duplicated changelog sections: {dupes}")

    def test_every_entry_has_at_least_one_bullet(self):
        text = CHANGELOG.read_text(encoding="utf-8")
        sections = re.split(r"^## ", text, flags=re.M)[1:]
        empty = [s.splitlines()[0].strip() for s in sections
                 if not any(l.startswith("- ") for l in s.splitlines())]
        self.assertFalse(empty, f"changelog sections with no items: {empty}")

    def test_every_git_tag_is_documented(self):
        """Every released tag needs an entry, or is explicitly summarised."""
        tags = _git_tags()
        if tags is None:
            self.skipTest("git not available")
        documented = set(_changelog_versions()) | SUMMARISED
        missing = sorted(set(tags) - documented, key=_key)
        self.assertFalse(
            missing,
            f"released but absent from CHANGELOG.md: {missing}. Add a section "
            f"for each, or list it in SUMMARISED if it is covered by the "
            f"'גרסאות קודמות' heading.")

    def test_no_changelog_entry_without_a_release(self):
        """Catches a version documented but never tagged — except the one being
        prepared right now, which is expected to have no tag yet."""
        tags = _git_tags()
        if tags is None:
            self.skipTest("git not available")
        untagged = [v for v in _changelog_versions()
                    if v not in tags and v != __version__]
        self.assertFalse(untagged,
                         f"in CHANGELOG.md but never released: {untagged}")


class ChangelogRenderTestCase(unittest.TestCase):
    """The dialog must actually parse what we wrote."""

    def test_dialog_parses_and_marks_the_current_version(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import theme
        from ui import ChangelogDialog

        parsed = ChangelogDialog(None, theme.LIGHT)._parse_versions()
        vers = [v["ver"] for v in parsed if v["ver"]]
        self.assertEqual(vers, _changelog_versions(),
                         "the dialog parses a different set than the raw file")
        current = [v for v in parsed if v["ver"] == __version__]
        self.assertEqual(len(current), 1)
        self.assertTrue(current[0]["items"],
                        "the current version renders with no bullet points")


if __name__ == "__main__":
    unittest.main()
