# Vigile IA Discord — V1.0

Bot autonome de contrôle des arrivées : lecture OCR des captures de profil, surnom Discord, rôle Membres, alertes et boutons de décision pour le staff.

## Configuration Discord

1. Créer une application et un bot dans https://discord.com/developers/applications.
2. Dans **Bot**, activer les **Privileged Gateway Intents** : **Server Members Intent** et **Message Content Intent**.
3. Inviter le bot avec les permissions : Voir les salons, Lire l'historique, Envoyer des messages, Joindre des fichiers, Gérer les pseudos, Gérer les rôles. Le rôle du bot doit être **au-dessus** du rôle `Membres` et des rôles des utilisateurs qu'il doit renommer. Ne pas lui accorder Administrateur par défaut.
4. Vérifier qu'il peut accéder au salons Vigile `1522707744828821765` et Test `1560371205939396759` et au salon privé Modo `1531616175627173928`.
5. Dans Coolify, déployer ce dossier comme application **Dockerfile** (service permanent, **pas** de port HTTP nécessaire).
6. Définir `DISCORD_TOKEN` dans les variables d'environnement Coolify (secret). Les autres variables figurent dans `.env.example` et possèdent des valeurs par défaut pour les identifiants Discord.
7. Monter un **volume persistant** sur `/app/data` pour conserver SQLite après redéploiement. **Une seule instance/réplique** du bot.
8. Démarrer. Les logs doivent afficher `Connecté en tant que ...`.

## Test de validation

- Nouveau compte sans rôle : envoie une capture complète (PNG/JPEG/WebP) de l'interface « Informations joueur » dans le salon Vigile ou Test.
- Si le pseudo est lisible avec une confiance suffisante et qu'aucun doublon n'est connu : surnom + rôle `Membres` attribués.
- Sinon : le staff reçoit dans Modo l'image, le nom proposé, la raison et trois boutons (Valider, Refuser, Nouvelle capture).
- Les boutons sont réservés aux membres ayant **Gérer le serveur**, ou au rôle `STAFF_ROLE_ID` si défini. Le rôle Modo doit rester privé.
- Les décisions et validations sont enregistrées en SQLite.

## Limites importantes

- OCR calibré pour **la capture de référence 720×416** et ses versions redimensionnées. Un autre cadrage, thème ou une interface différente peut exiger une adaptation de `ocr.py`.
- Une capture ne démontre **pas** la propriété du compte de jeu ; un utilisateur peut soumettre la capture d'autrui. Le contrôle de doublons est une protection partielle, pas une authentification. Pour une vérification forte, prévoir une preuve supplémentaire contrôlée dans le jeu.
- L'IA n'authentifie pas les images modifiées : ce programme détecte des anomalies de lecture et des doublons, **pas** les falsifications sophistiquées.
- Le rôle est attribué uniquement après modification réussie du surnom. En cas d'échec d'attribution du rôle, le surnom peut avoir été modifié ; le dossier passe au staff.
- SQLite ne conserve pas les images elles-mêmes : les captures sont envoyées au salon Modo pour revue. Protéger l'accès à ce salon et prévoir une politique de conservation.
- L'API Discord et la hiérarchie des rôles peuvent empêcher de modifier certains comptes.
- Les messages traités sont ceux envoyés **après le démarrage** du bot, pas l'historique.
- Si le bot redémarre pendant un incident d'envoi au salon Modo, un dossier peut rester en attente sans message d'alerte ; une intervention manuelle peut être nécessaire.

## Développement local

Python 3.12 et Tesseract OCR installé, puis :

```bash
pip install -r requirements.txt
cp .env.example .env
# Éditer .env et renseigner DISCORD_TOKEN
python bot.py
```

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

## Salon de test (V1.1)

Les captures sont acceptées dans les deux salons configurés par `VIGILE_CHANNEL_IDS` (IDs séparés par des virgules). Le salon Test exécute les mêmes actions réelles que le salon principal, y compris l’attribution du rôle et le changement de surnom. Les alertes restent centralisées dans le salon Modo.


## V2 — Portail public et parcours privé

- Le bot publie **un portail persistant** dans les deux salons `VIGILE_CHANNEL_IDS` au démarrage (pas de doublons si la base SQLite est conservée).
- Le bouton **Commencer ma vérification** crée un salon texte privé, visible par le joueur, le bot et les administrateurs Discord (et toute personne disposant de permissions serveur qui contournent les restrictions de salons).
- Le joueur accepte le règlement, envoie une capture, puis confirme le pseudo détecté. Une anomalie bloque la validation automatique et ouvre un dossier dans le salon Modo.
- Le salon privé reste disponible après validation pour conserver une trace; il peut être supprimé manuellement par le staff.
- Les anciennes captures déposées directement dans les salons publics ne déclenchent plus la validation.

### Permissions supplémentaires

Donner au bot **Gérer les salons** en plus de **Gérer les pseudos**, **Gérer les rôles**, **Voir les salons**, **Envoyer des messages**, **Lire l'historique**, **Joindre des fichiers** et **Intégrer des liens**. Activer les intents **Server Members** et **Message Content** dans le portail développeur Discord. Placer le rôle du bot au-dessus du rôle Membres et des membres qu'il doit renommer.

### Important

Les salons privés sont créés dans la catégorie du salon portail, mais avec des permissions spécifiques. Les administrateurs et personnes dotées de permissions de contournement peuvent y accéder. Les captures ne prouvent pas la propriété du compte de jeu. Les permissions et le parcours Discord doivent être testés après déploiement.

## V2.1 — Nom du salon et fermeture automatique

- Le salon privé utilise le pseudo Discord actuel du joueur (`verification-pseudo`) plutôt que son identifiant numérique. Les caractères incompatibles sont normalisés.
- Après validation automatique ou validation par un modérateur, le salon temporaire est supprimé après 10 secondes et la session est nettoyée.
- Après refus par le staff, le salon est également supprimé ; une demande de nouvelle capture conserve le salon ouvert.
- Le salon public Vigile et le salon Modo ne sont jamais supprimés.
- Si Discord refuse la suppression, l'erreur est journalisée et le salon reste ouvert pour intervention manuelle.


## V2.2 — Alertes de test séparées

Les vérifications démarrées depuis `VIGILE_TEST_CHANNEL_ID` envoient leurs alertes et boutons de modération vers `MOD_TEST_CHANNEL_ID`. Les autres vérifications utilisent `MOD_CHANNEL_ID`. L'origine du parcours est enregistrée dans SQLite dès la création du salon privé et conservée pendant la vérification. La migration de base de données est automatique. Les anciennes sessions créées avant cette version utilisent le salon Modo principal (origine inconnue).

## V2.3 — Deux essais avant alerte staff
- Première capture non reconnue, suspecte ou en conflit : demande d'une deuxième capture, sans alerte modo.
- Deuxième capture problématique : dossier `pending`, alerte dans Modo Test ou Modo principal selon le portail d'origine, avec liens vers les deux captures.
- Si le joueur refuse le pseudo OCR à la deuxième tentative : transmission au staff.
- Bouton **Valider** : ouvre une fenêtre permettant au modérateur de saisir ou corriger le pseudo exact avant d'attribuer le rôle Membres.
- Bouton **Refuser** : aucun rôle, fermeture du salon privé après 10 secondes ; le dossier reste dans le salon Modo.
- Les compteurs et les URLs des captures sont persistés dans SQLite (`/app/data` à monter en volume persistant sur Coolify).
- Pour les sessions déjà ouvertes avant la V2.3, le compteur commence à zéro.

Note : l'OCR ne prouve pas la propriété du compte de jeu. Les contrôles de duplication bloquent la validation même par le staff ; un administrateur doit résoudre les conflits manuellement.
