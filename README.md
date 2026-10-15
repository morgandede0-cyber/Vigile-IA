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


## V2.7 — Correction commande /vigile-portail
- Synchronisation des commandes sur chaque serveur au démarrage.
- Démarrage du contrôle périodique des portails.
- Autorisation : Gérer le serveur, Gérer les salons ou rôle STAFF_ROLE_ID.
- Logs Coolify détaillés en cas d’erreur.
- Vérifier le scope Discord applications.commands si la commande est absente.


V2.8 : `/vigile-portail` est autorisé dans le salon principal et le salon test (ID 1560371205939396759). Le salon test est toujours ajouté aux salons surveillés même si la variable VIGILE_CHANNEL_IDS ne le mentionne pas. `/vigile-reset` reste réservé au salon test.


## V2.9 — Rôle Membres Test
- Salon Vigile Test (`1560371205939396759`) : rôle `1552119593038909501`.
- Salon Vigile principal : rôle `1522696283595472916`.
- Variable Coolify facultative `MEMBER_TEST_ROLE_ID=1552119593038909501`.
- Le bot doit avoir le droit Gérer les rôles et un rôle supérieur aux deux rôles cibles.


## V3.0 — Protection anti-doublons
- Chaque vérification scanne les 1000 derniers messages du salon Vigile (principal et test).
- Reconnaît uniquement les messages du bot portant le bouton `vigile:portal:start`.
- Conserve le portail enregistré dans SQLite, sinon le plus récent, et supprime les autres.
- Reconstitue le suivi SQLite si la base a été recréée.
- Si la lecture de l’historique échoue, ne republie rien afin de ne pas créer de doublon.
- Exige les permissions Voir le salon, Lire l’historique, Envoyer des messages et Gérer les messages (suppression des doublons).
- La commande `/vigile-portail` permet aussi de nettoyer les doublons.
- Limite : les portails situés au-delà des 1000 derniers messages ne sont pas inspectés.

## V3.1 — Validation modérateur et OCR
- Corrige l'erreur de validation modérateur causée par l'accès `.get()` sur un `sqlite3.Row` : le rôle dépend maintenant directement de l'origine du dossier, même si la session a disparu.
- Recherche les membres absents du cache Discord via `fetch_member`.
- Supprime le message d'alerte et ses captures du salon staff après une validation ou un refus **réussi**. L'historique SQLite reste conservé.
- OCR ciblé sur le pseudo jaune sous « Profil », avec masquage des libellés blancs. Les mots d'interface comme « Profil » sont interdits comme pseudo.
- La validation échouée conserve le dossier en attente pour permettre une nouvelle tentative.
- Permissions nécessaires : Gérer les pseudos, Gérer les rôles, Gérer les messages ; le rôle du bot doit être au-dessus du joueur et du rôle attribué. Pour les modérateurs, Gérer le serveur ou Gérer les salons ou STAFF_ROLE_ID.
- Tester les deux parcours (automatique et validation staff) sur Discord ; les tests unitaires ne simulent pas les permissions Discord ni une capture réelle de jeu.


## V3.2 — Accès direct au salon privé
Après le clic sur Commencer ma vérification, le bot fournit immédiatement un bouton-lien vers le salon privé créé. Si une session existe, le même bouton est affiché. Discord ne permet pas aux bots de forcer le client d’un utilisateur à changer de salon : un clic reste nécessaire.


## V3.3 — Accueil bilingue
Le portail affiche le nouveau texte de bienvenue IV en français. Deux boutons 🇫🇷 Français et 🇬🇧 English ouvrent une version traduite visible uniquement par le joueur, avec un bouton de démarrage. Le bouton de démarrage public reste disponible. Les portails existants sont édités sur place au redémarrage (pas de nouveau message). Les étapes de vérification privées restent en français dans cette version.


## V3.4 — Règlement condensé FR/EN
- Règlement complet remplacé par la version courte validée.
- Traduction anglaise selon le choix du portail.
- Langue persistée dans SQLite (migration non destructive).
- Bouton d’acceptation traduit ; les autres étapes restent en français.


## V3.5 — Un seul sélecteur de langue
Le portail public affiche un bouton 🌐 EN sur la première rangée et un bouton Commencer sur la seconde. Le bouton langue ouvre un affichage privé où un seul bouton bascule FR/EN, avec démarrage dans la langue choisie. La langue est enregistrée en base dans sessions.language et utilisée pour le règlement et les instructions de capture. Discord ne permet pas de placer les boutons au coin supérieur droit du texte ; la première rangée est le placement le plus proche possible sans Activity. Les outils de modération et certains messages techniques restent en français.


## V3.6 — Portail Components V2 et anti-doublons
- Bouton 🇫🇷 en accessoire à droite du titre (Section Components V2).
- Un clic ouvre le choix de langue privé 🇫🇷 / 🇬🇧, sans modifier le portail des autres.
- Le choix de langue s'applique au parcours de vérification, comme dans V3.5.
- Nettoyage des anciens portails du bot en scannant les 500 derniers messages et l'ancien message enregistré en SQLite.
- Un seul portail Components V2 conservé par salon, sans toucher aux messages des utilisateurs.
- Nécessite les permissions Lire l'historique des messages, Envoyer des messages, Gérer les messages.
- Les tests unitaires ne remplacent pas un test réel Discord Components V2.
