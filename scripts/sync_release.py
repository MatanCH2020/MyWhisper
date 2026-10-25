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
    version = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+(?:b\d+)?)"', source).group(1)
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
    if "b" in version:
        # Beta release links are separate from the published stable Windows blocks.
        readme = ROOT / "README.md"
        homepage = ROOT / "docs/index.html"
        url = f"{REPO}/releases/download/{tag}/MyWhisper-{version}-macos-arm64.dmg"
        beta_md = f"**בטא ל־Mac: {version}** · [הורדת DMG]({url}) · [מדריך ובדיקות](docs/macos-beta.md)\n\nApple Silicon M1 ומעלה · 16GB ומעלה · macOS 15 ומעלה · התקנה מחוץ לחנות · ללא ChatGPT. טרם אומת על מחשבי בודקים."
        beta_html = f'''<article class="inst" id="mac-beta" data-mac-beta="{version}" aria-labelledby="mac-install-title">
      <span class="eyebrow">Apple Silicon · בטא לבודקים</span>
      <h3 id="mac-install-title">Mac · <bdi>{version}</bdi></h3>
      <p>M1 ומעלה · 16GB ומעלה · macOS 15 ומעלה.</p>
      <div class="cta-row"><a class="btn btn-primary" href="{url}">הורדת DMG ל־Mac</a><a class="btn btn-ghost" href="{REPO}/blob/{tag}/docs/macos-beta.md">מדריך התקנה ובדיקות</a></div>
      <ol class="platform-steps">
        <li>פותחים את ה־DMG וגוררים את MyWhisper לתיקיית Applications.</li>
        <li>מפעילים משם. אם macOS חוסם את הפתיחה, משתמשים ב״פתח בכל זאת״ בפרטיות ואבטחה, לפי המדריך.</li>
        <li>מאשרים מיקרופון ונגישות, ומורידים את המודל דרך מסך ההכנה (כ־3.08GB).</li>
      </ol>
      <p>קיצור תמלול: <kbd dir="ltr">Control + Option + Space</kbd>. אין צורך ב־Python או Homebrew. לאחר הורדת המודל התמלול מקומי, ללא ChatGPT.</p>
      <p class="platform-note">בטא ניסיונית, ללא notarization של Apple. נדרשות עדיין בדיקות חומרה אצל בודקים. עדכון מתבצע בהורדת DMG חדש ובהחלפת האפליקציה לאחר סגירתה.</p>
    </article>'''
        stale = []
        for path, content in ((readme, beta_md), (homepage, beta_html)):
            old = path.read_text(encoding="utf-8")
            new = replace_block(old, "mac-beta", content)
            if new != old:
                stale.append(path.name)
                if not args.check:
                    path.write_text(new, encoding="utf-8")
        if stale and args.check:
            raise ValueError(f"Stale Mac beta information: {stale}")
        if args.notes:
            if args.check:
                raise ValueError("--check cannot write notes")
            args.notes.write_text(notes + "\n", encoding="utf-8")
        print(f"Mac beta {tag}: metadata synchronized; stable Windows links preserved")
        return
    items = []
    for line in [line[2:] for line in notes.splitlines() if line.startswith("- ")][:6]:
        escaped = html.escape(line)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        items.append(f"        <li>{escaped}</li>")
    section = f'''<section id="updates" data-release-version="{version}">
  <div class="wrap">
    <span class="eyebrow">מה חדש</span>
    <h2 class="h2">גרסת Windows היציבה: <bdi>{tag}</bdi></h2>
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
