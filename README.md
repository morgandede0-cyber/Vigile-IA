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

    def test_test_reset_is_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            db = Storage(Path(d)/'test.sqlite3')
            test_case = db.create_case(1, 10, 100, 'h1', 'url', 'Test', 95, 'OK', 222)
            prod_case = db.create_case(1, 20, 200, 'h2', 'url', 'Prod', 95, 'OK', 111)
            self.assertTrue(db.approve(test_case, 'Test'))
            self.assertTrue(db.approve(prod_case, 'Prod'))
            db.db.execute("CREATE TABLE sessions (guild_id INTEGER, user_id INTEGER, channel_id INTEGER, origin_channel_id INTEGER)")
            db.db.execute("INSERT INTO sessions VALUES (1,10,300,222)")
            db.db.execute("INSERT INTO sessions VALUES (1,20,400,111)")
            a,p,channels = db.reset_test(1,222,999)
            self.assertEqual((a,p,channels),(1,0,[300]))
            self.assertIsNone(db.approved_user(1,10))
            self.assertTrue(db.approved_user(1,20))
            self.assertEqual(db.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],1)

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



## V2.4 — Commande de réinitialisation TEST

Dans le salon **Vigile Test** (`1560371205939396759`), un modérateur ayant
la permission « Gérer le serveur » (ou le rôle STAFF_ROLE_ID) peut lancer
`/vigile-reset`. La commande refuse de fonctionner dans les autres salons.
Elle efface les validations **provenant du portail test**, clôt les dossiers
TEST en attente et supprime les salons privés TEST encore ouverts. Elle ne
retire pas les rôles Discord et ne touche pas aux validations du portail réel.
Les anciens dossiers antérieurs à V2.4 sans provenance enregistrée ne sont
pas réinitialisés par sécurité. La commande slash peut être visible ailleurs,
mais son exécution y est interdite. Les décisions passées restent dans l'audit.

## V2.5 — Protection du portail

Les portails publics Vigile (principal et test) sont restaurés automatiquement après suppression du message, y compris lors d'une suppression groupée. L'événement de suppression est reçu via les événements raw Discord, indépendamment du cache des messages. Une attente de 1 seconde précède la republication; le délai réel peut dépasser 5 secondes en cas de latence ou d'indisponibilité Discord.

Le bot vérifie l'identifiant du portail enregistré dans SQLite, évite les republications concurrentes et restaure également un portail manquant au redémarrage. Nécessite les permissions Voir le salon, Lire l'historique et Envoyer des messages dans les deux salons Vigile.
