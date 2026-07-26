"""Unit tests for the id-based history store."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import history


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = history.HISTORY_PATH
        history.HISTORY_PATH = Path(self._tmp.name) / "history.json"

    def tearDown(self):
        history.HISTORY_PATH = self._orig
        self._tmp.cleanup()

    def test_add_assigns_unique_ids_newest_first(self):
        history.add("ראשון")
        history.add("שני")
        entries = history.load()
        self.assertEqual([e["text"] for e in entries], ["שני", "ראשון"])
        self.assertNotEqual(entries[0]["id"], entries[1]["id"])

    def test_update_by_id_survives_new_entries(self):
        history.add("ישן")
        target_id = history.load()[0]["id"]
        history.add("חדש")  # shifts positions — id must still hit the old entry
        history.update(target_id, "ישן-מתוקן")
        texts = {e["id"]: e["text"] for e in history.load()}
        self.assertEqual(texts[target_id], "ישן-מתוקן")

    def test_delete_by_id(self):
        history.add("להשאיר")
        history.add("למחוק")
        doomed = history.load()[0]["id"]
        history.delete(doomed)
        entries = history.load()
        self.assertEqual([e["text"] for e in entries], ["להשאיר"])

    def test_delete_returns_entry_and_index_for_undo(self):
        history.add("א")
        history.add("ב")
        history.add("ג")
        target = history.load()[1]  # the middle one
        removed = history.delete(target["id"])
        self.assertIsNotNone(removed)
        entry, index = removed
        self.assertEqual(entry["text"], "ב")
        self.assertEqual(index, 1)

    def test_delete_missing_id_returns_none(self):
        history.add("קיים")
        self.assertIsNone(history.delete("nosuchid"))

    def test_restore_puts_entry_back_at_its_position(self):
        for t in ("א", "ב", "ג"):
            history.add(t)
        # newest-first order is now ["ג", "ב", "א"]
        entry, index = history.delete(history.load()[1]["id"])
        history.restore(entry, index)
        self.assertEqual([e["text"] for e in history.load()], ["ג", "ב", "א"])

    def test_restore_is_idempotent(self):
        history.add("יחיד")
        entry, index = history.delete(history.load()[0]["id"])
        history.restore(entry, index)
        history.restore(entry, index)  # a second undo must not duplicate
        self.assertEqual(len(history.load()), 1)

    def test_restore_ignores_junk(self):
        history.add("קיים")
        history.restore(None, 0)
        history.restore({}, 0)          # no id
        history.restore({"text": "x"}, 0)
        self.assertEqual(len(history.load()), 1)

    def test_restore_out_of_range_index_is_clamped(self):
        history.add("א")
        entry, _ = history.delete(history.load()[0]["id"])
        history.restore(entry, 999)
        self.assertEqual([e["text"] for e in history.load()], ["א"])

    def test_legacy_entries_get_ids_on_load(self):
        legacy = [{"time": "2026-01-01 10:00", "text": "בלי מזהה"}]
        with open(history.HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False)
        entries = history.load()
        self.assertTrue(entries[0].get("id"))
        # The migration is persisted.
        with open(history.HISTORY_PATH, "r", encoding="utf-8") as f:
            self.assertTrue(json.load(f)[0].get("id"))

    def test_cap(self):
        for i in range(history.MAX_ENTRIES + 10):
            history.add(f"רשומה {i}")
        self.assertEqual(len(history.load()), history.MAX_ENTRIES)


if __name__ == "__main__":
    unittest.main()
