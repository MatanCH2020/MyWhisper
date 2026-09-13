"""Sync public release information from version.py + CHANGELOG.md; CI uses --check."""
import argparse
import html
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/MatanCH2020/MyWhisper"


def release_content():
    source = (ROOT / "app/version.py").read_text(encoding="utf-8")
    version = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', source).group(1)
    sections = re.split(r"^## ", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), flags=re.M)
    heading, notes = sections[1].split("\n", 1)
    if heading.strip() != f"גרסה {version}":
        raise ValueError("Version and newest changelog entry disagree")
    return version, notes.strip()


def replace_block(source, marker, content):
    pattern = rf"<!-- {marker}:start -->.*?<!-- {marker}:end -->"
    result, count = re.subn(pattern, lambda _: f"<!-- {marker}:start -->\n{content}\n<!-- {marker}:end -->",
                            source, flags=re.S)
    if count != 1:
        raise ValueError(f"Expected exactly one {marker} block")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail on stale generated content; never write")
    parser.add_argument("--notes", type=Path, help="Write the current changelog section for gh release --notes-file")
    args = parser.parse_args()
    version, notes = release_content()
    tag = f"v{version}"
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/") and ref != f"refs/tags/{tag}":
        raise ValueError("Git tag and application version disagree")
    # A published application cannot silently change under the same version.
    tagged = subprocess.run(["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
                            cwd=ROOT, capture_output=True)
    if tagged.returncode == 0:
        changed = subprocess.run(["git", "diff", "--quiet", tag, "--", "app"], cwd=ROOT)
        if changed.returncode:
            raise ValueError(f"Application changed since {tag}; bump version.py and add a changelog section")
    items = []
    for line in [line[2:] for line in notes.splitlines() if line.startswith("- ")][:6]:
        escaped = html.escape(line)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        items.append(f"        <li>{escaped}</li>")
    section = f'''<section id="updates" data-release-version="{version}">
  <div class="wrap">
    <span class="eyebrow">מה חדש</span>
    <h2 class="h2">הגרסה העדכנית: <bdi>{tag}</bdi></h2>
    <p class="sub">העדכונים האחרונים מתוך יומן השינויים של התוכנה.</p>
    <div class="release-notes">
      <ul>
{chr(10).join(items)}
      </ul>
    </div>
    <div class="cta-row">
      <a class="btn btn-lg btn-primary" href="{REPO}/releases/download/{tag}/MyWhisper-Setup.cmd">הורדת המתקין</a>
      <a class="btn btn-lg btn-ghost" href="{REPO}/releases/tag/{tag}">כל פרטי הגרסה</a>
    </div>
  </div>
</section>'''
    homepage = ROOT / "docs/index.html"
    readme = ROOT / "README.md"
    original = homepage.read_text(encoding="utf-8")
    updated = replace_block(original, "release", section)
    updated = re.sub(r'(src="app-[^"?]+\.png)(?:\?v=[^" ]+)?"',
                     lambda m: f'{m[1]}?v={version}"', updated)
    public_version = f"**גרסה נוכחית: [{tag}]({REPO}/releases/tag/{tag})** · [היסטוריית העדכונים](CHANGELOG.md)"
    stale = []
    for path, new in ((homepage, updated), (readme, replace_block(readme.read_text(encoding="utf-8"),
                                                                "release-version", public_version))):
        if path.read_text(encoding="utf-8") != new:
            stale.append(str(path.relative_to(ROOT)))
            if not args.check:
                path.write_text(new, encoding="utf-8")
    if stale and args.check:
        raise ValueError(f"Stale release information: {', '.join(stale)}. Run scripts/sync_release.py")
    if args.notes:
        if args.check:
            raise ValueError("--check cannot write release notes")
        args.notes.write_text(notes + "\n", encoding="utf-8")
    print(f"Release {tag}: version, changelog, README and website are synchronized")


if __name__ == "__main__":
    main()
