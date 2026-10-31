"""Exercise update transactions in disposable Git repositories and fake venvs."""
from pathlib import Path
from contextlib import contextmanager
import json
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
        def fail(root, progress):
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
        def install(root, progress):
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

    def test_graphical_handoff_reuses_complete_backup_and_reports_rollback(self):
        percentages = []
        tag, backup_id = updater.prepare_window_update(self.root, percentages.append, "v1.1.0")
        self.assertEqual(percentages[0], 0)
        self.assertEqual(percentages[-1], 100)
        self.assertEqual(percentages, sorted(set(percentages)))
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
        stages = []
        with patch.object(updater, "_backup", side_effect=AssertionError("duplicate backup")), \
             patch.object(updater, "_install", side_effect=RuntimeError("install failed")):
            with self.assertRaisesRegex(RuntimeError, "previous version restored"):
                updater.apply_update(self.root, tag, backup_id=backup_id, progress=stages.append)
        self.assertEqual(stages, ["install", "restore"])
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
        self.assertEqual((self.root / ".venv/marker").read_text(), "old environment")

    def test_graphical_handoff_rejects_changed_checkout_before_installing(self):
        _, backup_id = updater.prepare_window_update(self.root, lambda pct: None, "v1.1.0")
        self.git("checkout", "-b", "changed")
        with self.assertRaisesRegex(RuntimeError, "changed since backup"):
            updater.apply_update(self.root, "v1.1.0", backup_id=backup_id)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)

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


class RestartPolicyTest(unittest.TestCase):
    def test_only_explicit_false_disables_restart_without_rewriting_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertTrue(updater._restart_enabled(root))
            for value in (False, True, None, 0, 'false'):
                data = json.dumps({'restart_after_update': value, 'other': 'keep'})
                (root / 'config.json').write_text(data, encoding='utf-8')
                self.assertEqual(updater._restart_enabled(root), value is not False)
                self.assertEqual((root / 'config.json').read_text(), data)
            (root / 'config.json').write_text('{broken', encoding='utf-8')
            self.assertTrue(updater._restart_enabled(root))

    def test_success_rollback_opt_out_and_failed_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            # Windows runners may expose TEMP through an 8.3 short path.
            root = Path(folder).resolve()
            for enabled, no_restart, failure, recovered, expected in (
                (True, False, False, True, True),
                (False, False, False, True, False),
                (True, True, False, True, False),
                (True, False, True, True, True),
                (True, False, True, False, False),
            ):
                with self.subTest(enabled=enabled, no_restart=no_restart, failure=failure, recovered=recovered):
                    (root / 'config.json').write_text(json.dumps({'restart_after_update': enabled}))
                    held = []
                    @contextmanager
                    def guard(*args):
                        held.append(True)
                        try:
                            yield
                        finally:
                            held.pop()
                    def launch(*args, **kwargs):
                        self.assertFalse(held, 'Restart raced the updater mutex')
                        self.assertEqual(args[0], ['wscript.exe', str(root / 'scripts/run_mywishper.vbs')])
                        self.assertEqual(kwargs['cwd'], root)
                    argv = ['updater.py', '--root', str(root), '--tag', 'v1.0.0']
                    if no_restart:
                        argv.append('--no-restart')
                    with patch.object(sys, 'argv', argv), \
                         patch.object(updater, '_ensure_stopped'), \
                         patch.object(updater, '_app_guard', guard), \
                         patch.object(updater, '_update_lock', guard), \
                         patch.object(updater, 'prepare', return_value='v1.0.0'), \
                         patch.object(updater, 'apply_update', side_effect=RuntimeError('synthetic failure') if failure else None), \
                         patch.object(updater, 'run', side_effect=None if recovered else RuntimeError('unusable')), \
                         patch.object(updater.subprocess, 'Popen', side_effect=launch) as start:
                        self.assertEqual(updater.main(), int(failure))
                    self.assertEqual(start.called, expected)


if __name__ == "__main__":
    unittest.main()
