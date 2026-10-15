import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ocr import clean_name

class OcrGuard(unittest.TestCase):
    def test_ui_words_rejected(self):
        for name in ('Profil','Profile','Saison','Conseillers','Informations joueur'):
            self.assertEqual(clean_name(name),'')
    def test_valid_name(self):
        self.assertEqual(clean_name('IV Rogne'),'IV Rogne')
