import tempfile
import unittest
from pathlib import Path
from sys import path
path.insert(0, str(Path(__file__).resolve().parents[1]))
from ocr import clean_name, extract_name
from storage import Storage

class CoreTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(clean_name(' IV   Rogne '), 'IV Rogne')
        self.assertEqual(clean_name(''), '')
        self.assertEqual(clean_name('a'*33), '')
        self.assertEqual(clean_name('@everyone'), '')

    def test_invalid_image(self):
        result = extract_name(b'not an image')
        self.assertIsNone(result.name)

    def test_storage(self):
        with tempfile.TemporaryDirectory() as d:
            db = Storage(Path(d)/'test.sqlite3')
            case = db.create_case(1, 2, 3, 'hash', 'url', 'IV Rogne', 95, 'OK')
            self.assertTrue(db.pending_for_user(1, 2))
            self.assertTrue(db.approve(case, 'IV Rogne'))
            self.assertIsNone(db.pending_for_user(1, 2))
            self.assertTrue(db.approved_user(1, 2))
            self.assertTrue(db.existing_name(1, 'iv rogne', 4))
            self.assertTrue(db.reused_image(1, 'hash', 4))
            self.assertFalse(db.approve(case, 'IV Rogne'))

if __name__ == '__main__':
    unittest.main()

