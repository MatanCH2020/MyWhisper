"""Unit tests for the clipboard-history store."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import clips


class ClipsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig = (clips.CLIPS_PATH, clips.IMAGE_DIR)
        clips.CLIPS_PATH = root / "clips.json"
        clips.IMAGE_DIR = root / "clip_images"

    def tearDown(self):
        clips.CLIPS_PATH, clips.IMAGE_DIR = self._orig
        self._tmp.cleanup()

    # ---- text ----
    def test_add_text_newest_first(self):
        clips.add_text("ראשון")
        clips.add_text("שני")
        self.assertEqual([e["text"] for e in clips.load()], ["שני", "ראשון"])

    def test_blank_text_is_ignored(self):
        self.assertIsNone(clips.add_text(""))
        self.assertIsNone(clips.add_text("   "))
        self.assertIsNone(clips.add_text(None))
        self.assertEqual(clips.load(), [])

    def test_recopy_moves_to_top_without_duplicating(self):
        clips.add_text("א")
        clips.add_text("ב")
        clips.add_text("א")  # copied again
        entries = clips.load()
        self.assertEqual([e["text"] for e in entries], ["א", "ב"])
        self.assertEqual(len(entries), 2)

    def test_oversized_text_is_skipped(self):
        self.assertIsNone(clips.add_text("x" * (clips.MAX_TEXT_CHARS + 1)))
        self.assertEqual(clips.load(), [])

    def test_text_at_the_cap_is_kept(self):
        self.assertIsNotNone(clips.add_text("x" * clips.MAX_TEXT_CHARS))
        self.assertEqual(len(clips.load()), 1)

    def test_text_count_is_not_capped_at_a_small_number(self):
        # The whole point of the feature: a long history. 500 is well past any
        # "keep the last N" limit a clipboard manager would normally impose.
        for i in range(500):
            clips.add_text(f"פריט {i}")
        self.assertEqual(len(clips.load()), 500)

    def test_entries_have_ids_and_times(self):
        e = clips.add_text("בדיקה")
        self.assertTrue(e["id"])
        self.assertTrue(e["time"])
        self.assertEqual(e["kind"], "text")

    # ---- images ----
    def _fake_png(self, path):
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return True

    def test_add_image_stores_a_file(self):
        e = clips.add_image(self._fake_png)
        self.assertEqual(e["kind"], "image")
        self.assertTrue(Path(e["path"]).exists())

    def test_image_cap_evicts_oldest_and_deletes_files(self):
        made = [clips.add_image(self._fake_png)
                for _ in range(clips.MAX_IMAGES + 3)]
        images = [e for e in clips.load() if e["kind"] == "image"]
        self.assertEqual(len(images), clips.MAX_IMAGES)
        for old in made[:3]:
            self.assertFalse(Path(old["path"]).exists(),
                             "evicted image file should be deleted")

    def test_image_cap_does_not_evict_text(self):
        clips.add_text("לשמור")
        for _ in range(clips.MAX_IMAGES + 5):
            clips.add_image(self._fake_png)
        texts = [e for e in clips.load() if e["kind"] == "text"]
        self.assertEqual([e["text"] for e in texts], ["לשמור"])

    def test_failed_image_save_records_nothing(self):
        self.assertIsNone(clips.add_image(lambda p: False))
        self.assertEqual(clips.load(), [])

    # ---- delete / restore / clear ----
    def test_delete_returns_entry_and_index(self):
        for t in ("א", "ב", "ג"):
            clips.add_text(t)
        target = clips.load()[1]
        entry, index = clips.delete(target["id"])
        self.assertEqual(entry["text"], "ב")
        self.assertEqual(index, 1)

    def test_delete_missing_returns_none(self):
        self.assertIsNone(clips.delete("nope"))

    def test_restore_puts_it_back(self):
        for t in ("א", "ב", "ג"):
            clips.add_text(t)
        entry, index = clips.delete(clips.load()[1]["id"])
        clips.restore(entry, index)
        self.assertEqual([e["text"] for e in clips.load()], ["ג", "ב", "א"])

    def test_restore_is_idempotent(self):
        clips.add_text("יחיד")
        entry, index = clips.delete(clips.load()[0]["id"])
        clips.restore(entry, index)
        clips.restore(entry, index)
        self.assertEqual(len(clips.load()), 1)

    def test_clear_removes_entries_and_image_files(self):
        clips.add_text("טקסט")
        img = clips.add_image(self._fake_png)
        clips.clear()
        self.assertEqual(clips.load(), [])
        self.assertFalse(Path(img["path"]).exists())

    # ---- search ----
    def test_search_is_case_insensitive_substring(self):
        clips.add_text("Hello World")
        clips.add_text("שלום עולם")
        self.assertEqual(len(clips.search("hello")), 1)
        self.assertEqual(len(clips.search("עולם")), 1)
        self.assertEqual(len(clips.search("nope")), 0)

    def test_empty_search_returns_everything(self):
        clips.add_text("א")
        clips.add_image(self._fake_png)
        self.assertEqual(len(clips.search("")), 2)

    def test_search_excludes_images(self):
        clips.add_image(self._fake_png)
        clips.add_text("טקסט")
        self.assertEqual([e["kind"] for e in clips.search("טקסט")], ["text"])

    # ---- preview ----
    def test_preview_collapses_whitespace_and_truncates(self):
        clips.add_text("שורה\nשנייה   עם    רווחים")
        p = clips.preview(clips.load()[0])
        self.assertNotIn("\n", p)
        self.assertIn("שורה שנייה", p)

    def test_preview_truncates_long_text(self):
        e = clips.add_text("א" * 300)
        p = clips.preview(e, limit=50)
        self.assertEqual(len(p), 50)
        self.assertTrue(p.endswith("…"))

    def test_preview_labels_images(self):
        self.assertIn("תמונה", clips.preview(clips.add_image(self._fake_png)))


if __name__ == "__main__":
    unittest.main()
