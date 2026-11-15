"""Проверки настроек (src/merci/settings.py).

Запуск из корня проекта:  python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        self.before = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.dir
        from merci.settings import Settings

        self.Settings = Settings

    def tearDown(self):
        if self.before is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.before
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_long_lived_copy_does_not_roll_back_other_writers(self):
        # Окно держит свой экземпляр всё время работы; ядро пишет через
        # другой. Запись окна не должна возвращать прежний размер окон.
        window = self.Settings()
        window.language = "ru"
        self.Settings().window_size = (1600, 900)
        window.updates_checked_at = 123.0
        self.assertEqual(self.Settings().window_size, (1600, 900))
        self.assertEqual(window.window_size, (1600, 900))
        self.assertEqual(self.Settings().language, "ru")

    def test_reads_follow_the_file(self):
        window = self.Settings()
        self.assertFalse(window.multiuser)
        self.Settings().multiuser = True
        self.assertTrue(window.multiuser)

    def test_parallel_writers_keep_every_key(self):
        def write(index):
            self.Settings().set(f"key{index}", index)

        threads = [threading.Thread(target=write, args=(index,)) for index in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with open(self.Settings().path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual({f"key{index}": index for index in range(24)}, data)

    def test_broken_file_is_no_settings(self):
        settings = self.Settings()
        os.makedirs(os.path.dirname(settings.path), exist_ok=True)
        with open(settings.path, "w") as handle:
            handle.write("{не json")
        self.assertEqual(self.Settings().window_size, (0, 0))
        self.Settings().eco_mode = True
        self.assertTrue(self.Settings().eco_mode)


if __name__ == "__main__":
    unittest.main()
