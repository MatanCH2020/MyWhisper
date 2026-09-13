"""Exercise update transactions in disposable Git repositories and fake venvs."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import updater


@unittest.skipUnless(shutil.which("git"), "Git required")
class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.git("init", "-b", "installed")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Update test")
        (self.root / "app").mkdir()
        (self.root / "scripts").mkdir()
        (self.root / ".gitignore").write_text(".venv/\n.update-backups/\nhistory.json\n")
        (self.root / "scripts/verify_install.py").write_text("pass\n")
        self.version("1.0.0")
        self.git("add", ".")
        self.git("commit", "-qm", "old")
        self.old = self.git("rev-parse", "HEAD")
        self.git("tag", "v1.0.0")
        self.git("checkout", "-b", "release")
        self.version("1.1.0")
        self.git("commit", "-qam", "new")
        self.git("tag", "v1.1.0")
        self.git("checkout", "installed")
        self.git("remote", "add", "origin", str(self.root))
        (self.root / ".venv").mkdir()
        (self.root / ".venv/marker").write_text("old environment")
        (self.root / "history.json").write_text('["private user data"]')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return updater.run(self.root, "git", *args)

    def version(self, value):
        (self.root / "app/version.py").write_text(f'__version__ = "{value}"\n')

    def test_dirty_tracked_or_untracked_files_block_update(self):
        self.version("9.9.9")
        with self.assertRaisesRegex(RuntimeError, "Local changes"):
            updater.prepare(self.root, "v1.1.0")
        self.assertIn("9.9.9", (self.root / "app/version.py").read_text())
        self.version("1.0.0")
        (self.root / "untracked.txt").write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "Local changes"):
            updater.apply_update(self.root, "v1.1.0")

    def test_offline_preflight_keeps_old_version(self):
        with patch.object(updater.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                updater.prepare(self.root)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
        self.assertFalse((self.root / ".update-backups").exists())

    def test_tag_version_mismatch_is_rejected(self):
        self.git("tag", "v7.7.7")
        with self.assertRaisesRegex(RuntimeError, "disagree"):
            updater.prepare(self.root, "v7.7.7")

    def test_dependency_failure_restores_code_and_environment(self):
        def fail(root):
            (root / ".venv/marker").write_text("partially installed")
            raise RuntimeError("pip failed")
        with patch.object(updater, "_install", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "previous version restored"):
                updater.apply_update(self.root, "v1.1.0")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
        self.assertEqual((self.root / ".venv/marker").read_text(), "old environment")
        self.assertEqual((self.root / "history.json").read_text(), '["private user data"]')
        self.assertTrue(list(self.root.glob(".update-backups/*/code.zip")))

    def test_success_installs_exact_tag_and_keeps_backup(self):
        def install(root):
            self.assertIn("1.1.0", (root / "app/version.py").read_text())
            (root / ".venv/marker").write_text("new environment")
        with patch.object(updater, "_install", side_effect=install):
            backup = updater.apply_update(self.root, "v1.1.0")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "v1.1.0"))
        self.assertEqual((backup / ".venv/marker").read_text(), "old environment")
        self.assertEqual((self.root / "history.json").read_text(), '["private user data"]')

    def test_paths_outside_installation_are_rejected(self):
        with self.assertRaises(ValueError):
            updater._child(self.root, self.root / "../other")
        with self.assertRaises(ValueError):
            updater._child(self.root, self.root)

    def test_native_failure_raises(self):
        with self.assertRaisesRegex(RuntimeError, r"failed \(7\)"):
            updater.run(self.root, sys.executable, "-c", "raise SystemExit(7)")


class PowerShellScriptsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell required")
    def test_scripts_parse_in_windows_powershell(self):
        root = Path(__file__).resolve().parent.parent
        for script in (root / "scripts").glob("*.ps1"):
            # Single-quoted literal paths, safely escaped for PowerShell.
            literal = str(script).replace("'", "''")
            command = ("$errs=$null; $tokens=$null; "
                       "[System.Management.Automation.Language.Parser]::ParseFile("
                       f"'{literal}', [ref]$tokens, [ref]$errs) | Out-Null; "
                       "if ($errs.Count) { $errs | Out-String | Write-Error; exit 1 }")
            result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True)
            self.assertEqual(result.returncode, 0, (script.name, result.stderr.decode(errors="replace")))


if __name__ == "__main__":
    unittest.main()
