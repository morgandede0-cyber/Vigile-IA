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

