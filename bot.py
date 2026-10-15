"""Vigile IA: vérification des captures de profil Discord.
La capture n'est PAS une preuve d'identité ou de propriété du compte de jeu.
"""
import asyncio
import hashlib
import io
import logging
import os
import re
import unicodedata
import sqlite3

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

from ocr import clean_name, extract_name
from storage import Storage

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vigile")

TOKEN = os.getenv("DISCORD_TOKEN", "")
VIGILE_CHANNELS = {int(x.strip()) for x in os.getenv("VIGILE_CHANNEL_IDS", "1522707744828821765,1560371205939396759").split(",") if x.strip()}
MOD = int(os.getenv("MOD_CHANNEL_ID", "1531616175627173928"))
MOD_TEST = int(os.getenv("MOD_TEST_CHANNEL_ID", "1560382339228377209"))
VIGILE_TEST = int(os.getenv("VIGILE_TEST_CHANNEL_ID", "1560371205939396759"))
# Le salon de test doit toujours être surveillé, même si VIGILE_CHANNEL_IDS ne le contient pas.
VIGILE_CHANNELS.add(VIGILE_TEST)
ROLE = int(os.getenv("MEMBER_ROLE_ID", "1522696283595472916"))
ROLE_TEST = int(os.getenv("MEMBER_TEST_ROLE_ID", "1552119593038909501"))
STAFF_ROLE = int(os.getenv("STAFF_ROLE_ID") or 0)
MAX_BYTES = int(os.getenv("MAX_IMAGE_BYTES", "8000000"))
MIN_CONFIDENCE = float(os.getenv("OCR_MIN_CONFIDENCE", "70"))
DB = Storage(os.getenv("DATABASE_PATH", "./data/vigile.sqlite3"))

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!vigile_unused_", intents=intents, help_command=None)
locks = {}
portal_repair_tasks = {}
portal_publish_locks = {}

# Portail public, salons de vérification privés persistants.
DB.db.executescript("""
CREATE TABLE IF NOT EXISTS portal_messages (channel_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 channel_id INTEGER NOT NULL UNIQUE, step TEXT NOT NULL,
 origin_channel_id INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(guild_id, user_id)
);
""")
DB.db.commit()
# Migration non destructive pour les installations V2.1 existantes.
if 'origin_channel_id' not in {r['name'] for r in DB.db.execute('PRAGMA table_info(sessions)')}:
    with DB.db:
        DB.db.execute('ALTER TABLE sessions ADD COLUMN origin_channel_id INTEGER NOT NULL DEFAULT 0')

if 'language' not in {r['name'] for r in DB.db.execute('PRAGMA table_info(sessions)')}:
    with DB.db:
        DB.db.execute("ALTER TABLE sessions ADD COLUMN language TEXT NOT NULL DEFAULT 'fr'")

# Migration des tentatives : persistance entre redémarrages.
for column, definition in {
    'attempts': 'INTEGER NOT NULL DEFAULT 0',
    'first_image_url': "TEXT NOT NULL DEFAULT ''",
    'second_image_url': "TEXT NOT NULL DEFAULT ''",
}.items():
    if column not in {r['name'] for r in DB.db.execute('PRAGMA table_info(sessions)')}:
        with DB.db:
            DB.db.execute(f'ALTER TABLE sessions ADD COLUMN {column} {definition}')


def increment_attempt(guild_id, user_id, url):
    with DB.db:
        record = session_for(guild_id, user_id)
        number = min(int(record['attempts']) + 1, 2)
        field = 'first_image_url' if number == 1 else 'second_image_url'
        DB.db.execute(f'UPDATE sessions SET attempts=?, {field}=? WHERE guild_id=? AND user_id=?',
                      (number, url, guild_id, user_id))
    return number


def retry_or_escalate(guild_id, user_id, reason):
    record = session_for(guild_id, user_id)
    if record and record['attempts'] < 2:
        set_step(guild_id, user_id, 'image')
        return True
    set_step(guild_id, user_id, 'pending')
    return False


def alert_channel_for_origin(origin_channel_id: int) -> int:
    return MOD_TEST if origin_channel_id == VIGILE_TEST else MOD


def alert_channel_for_case(case) -> int:
    session = session_for(case['guild_id'], case['user_id'])
    return alert_channel_for_origin(session['origin_channel_id'] if session else 0)


def session_for(guild_id, user_id):
    return DB.db.execute("SELECT * FROM sessions WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()


def session_channel(channel_id):
    return DB.db.execute("SELECT * FROM sessions WHERE channel_id=?", (channel_id,)).fetchone()


def set_step(guild_id, user_id, step):
    with DB.db:
        DB.db.execute("UPDATE sessions SET step=? WHERE guild_id=? AND user_id=?", (step, guild_id, user_id))


def private_channel_name(member: discord.Member) -> str:
    """Use the current server display name; Discord channel names must be safe."""
    name = unicodedata.normalize("NFKD", member.display_name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:75]
    return f"verification-{name or 'joueur'}"


async def close_session(guild_id: int, user_id: int, delay: int = 10):
    """Close the temporary channel after a decision; never delete other channels."""
    record = session_for(guild_id, user_id)
    if not record:
        return
    channel_id = record['channel_id']
    await asyncio.sleep(delay)
    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    if channel is not None:
        try:
            await channel.delete(reason="Vigile IA : vérification terminée")
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Impossible de fermer le salon privé %s", channel_id)
            return
    with DB.db:
        DB.db.execute("DELETE FROM sessions WHERE guild_id=? AND user_id=? AND channel_id=?", (guild_id, user_id, channel_id))


PORTAL_TEXT_FR = ("# 🛡️ Bienvenue sur ⌁ IV ⌁ | Million Lords\n\n"
    "**IV est bien plus qu’un clan : c’est une communauté** unie par la passion du jeu, l’entraide et l’aventure.\n\n"
    "Pour rejoindre nos rangs, **Vigile IA** vous accompagnera dans une courte vérification de votre identité en jeu.\n\n"
    "🔒 Votre vérification est privée, simple et sécurisée.\n\n"
    "**Toute l’équipe IV vous souhaite la bienvenue !**\n\n"
    "🛡️ **Commencez votre aventure dès maintenant.**")
PORTAL_TEXT_EN = ("# 🛡️ Welcome to ⌁ IV ⌁ | Million Lords\n\n"
    "**IV is more than a clan: it's a community** united by a passion for gaming, teamwork and adventure.\n\n"
    "To join our ranks, **Vigile IA** will guide you through a quick verification of your in-game identity.\n\n"
    "🔒 Your verification is private, simple and secure.\n\n"
    "**The entire IV team welcomes you!**\n\n"
    "🛡️ **Start your adventure now.**")


RULES_FR = ("# 📜 Règlement — ⌁ IV ⌁\n\n"
    "🤝 **Respect & convivialité**\nRespectez chaque membre. Harcèlement, discrimination, insultes et provocations sont interdits.\n\n"
    "💬 **Communication**\nRestez courtois, évitez les conflits publics et utilisez les salons appropriés.\n\n"
    "🚫 **Contenus interdits**\nSpam, publicités non autorisées, contenus NSFW ou choquants, liens malveillants, arnaques et usurpation d'identité.\n\n"
    "🛡️ **Staff & modération**\nRespectez les décisions du staff. En cas de problème, contactez **#・contact-staff**.\n\n"
    "⚖️ **Sanctions**\nTout manquement peut entraîner un avertissement, une exclusion temporaire, une suspension ou un bannissement définitif, selon la gravité.\n\n"
    "---\n✅ **En poursuivant votre vérification, vous acceptez ce règlement et vous engagez à le respecter.**")
RULES_EN = ("# 📜 Rules — ⌁ IV ⌁\n\n"
    "🤝 **Respect & community**\nRespect everyone. Harassment, discrimination, insults and provocation are forbidden.\n\n"
    "💬 **Communication**\nBe polite, avoid public arguments and use the appropriate channels.\n\n"
    "🚫 **Prohibited content**\nNo spam, unauthorized advertising, NSFW or shocking content, malicious links, scams or impersonation.\n\n"
    "🛡️ **Staff & moderation**\nRespect staff decisions. For concerns, contact **#・contact-staff**.\n\n"
    "⚖️ **Penalties**\nViolations may result in a warning, temporary removal, suspension or permanent ban depending on severity.\n\n"
    "---\n✅ **By continuing verification, you agree to follow these rules.**")


async def start_verification(interaction: discord.Interaction, language: str = "fr"):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Action disponible uniquement sur le serveur.", ephemeral=True)
    if interaction.channel_id not in VIGILE_CHANNELS:
        return await interaction.response.send_message("Utilise le portail dans le salon Vigile.", ephemeral=True)
    if DB.approved_user(interaction.guild.id, interaction.user.id):
        return await interaction.response.send_message("Tu es déjà membre validé.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild, member = interaction.guild, interaction.user
    existing = session_for(guild.id, member.id)
    if existing:
        channel = guild.get_channel(existing['channel_id'])
        if channel:
            jump = discord.ui.View(timeout=120)
            jump.add_item(discord.ui.Button(label="Open verification" if existing["language"] == "en" else "Ouvrir ma vérification", style=discord.ButtonStyle.link, url=channel.jump_url, emoji="➡️"))
            return await interaction.followup.send(
                "Your verification is already open. Use the button to join your private channel:"
                if existing["language"] == "en" else
                "Ta vérification est déjà ouverte. Clique pour rejoindre ton salon privé :",
                view=jump, ephemeral=True
            )
        with DB.db:
            DB.db.execute("DELETE FROM sessions WHERE guild_id=? AND user_id=?", (guild.id, member.id))
    me = guild.me
    if not me or not me.guild_permissions.manage_channels:
        return await interaction.followup.send("Impossible de créer ton espace privé. Préviens le staff (permission Gérer les salons manquante).", ephemeral=True)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    }
    try:
        channel = await guild.create_text_channel(
            name=private_channel_name(member),
            category=interaction.channel.category if isinstance(interaction.channel, discord.TextChannel) else None,
            overwrites=overwrites,
            topic=f"Vigile IA - vérification privée du membre {member.id}",
            reason="Vigile IA : création du parcours privé"
        )
    except discord.HTTPException as exc:
        log.exception("Création du salon privé impossible")
        return await interaction.followup.send(f"Impossible d'ouvrir la vérification : {exc}", ephemeral=True)
    with DB.db:
        DB.db.execute("INSERT INTO sessions(guild_id,user_id,channel_id,step,origin_channel_id,language) VALUES (?,?,?,?,?,?)", (guild.id, member.id, channel.id, 'rules', interaction.channel_id, language))
    await channel.send(f"{member.mention}\n" + (RULES_EN if language == "en" else RULES_FR), view=Rules(member.id, language))
    jump = discord.ui.View(timeout=120)
    jump.add_item(discord.ui.Button(label="➡️ Open verification" if language == "en" else "➡️ Accéder à ma vérification", style=discord.ButtonStyle.link, url=channel.jump_url))
    await interaction.followup.send(
        "**Your private channel is ready!** Use the button below to open it."
        if language == "en" else
        "**Ton salon privé est prêt !** Clique sur le bouton ci-dessous pour y accéder immédiatement.",
        view=jump, ephemeral=True
    )


class LanguageChoice(discord.ui.View):
    """Choix privé : un seul bouton bascule FR/EN, conservé au démarrage."""
    def __init__(self, lang: str = "fr"):
        super().__init__(timeout=180)
        self.lang = "en" if lang == "en" else "fr"
        self.children[0].label = "🇬🇧 English" if self.lang == "fr" else "🇫🇷 Français"
        self.children[1].label = "Start verification" if self.lang == "en" else "Commencer ma vérification"

    @discord.ui.button(label="🇬🇧 English", style=discord.ButtonStyle.secondary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.lang = "en" if self.lang == "fr" else "fr"
        self.children[0].label = "🇬🇧 English" if self.lang == "fr" else "🇫🇷 Français"
        self.children[1].label = "Start verification" if self.lang == "en" else "Commencer ma vérification"
        await interaction.response.edit_message(
            content=PORTAL_TEXT_EN if self.lang == "en" else PORTAL_TEXT_FR, view=self
        )

    @discord.ui.button(label="Commencer ma vérification", style=discord.ButtonStyle.success, emoji="🛡️", row=1)
    async def begin(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_verification(interaction, self.lang)


class Portal(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🌐 EN", style=discord.ButtonStyle.secondary, custom_id="vigile:portal:language", row=0)
    async def language(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Discord ne permet pas de placer un bouton au-dessus d'un texte :
        # première rangée du portail, choix privé sans modifier la langue des autres.
        await interaction.response.send_message(
            PORTAL_TEXT_FR, view=LanguageChoice("fr"), ephemeral=True
        )

    @discord.ui.button(label="Commencer ma vérification", style=discord.ButtonStyle.success, emoji="🛡️", custom_id="vigile:portal:start", row=1)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_verification(interaction, "fr")


class Rules(discord.ui.View):
    def __init__(self, user_id, language="fr"):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.children[0].label = "I agree and continue" if language == "en" else "J’accepte et je continue"
        self.children[0].custom_id = f"vigile:rules:{user_id}"

    @discord.ui.button(label="J'accepte et je continue", style=discord.ButtonStyle.primary, custom_id="vigile:rules:placeholder")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = session_channel(interaction.channel_id)
        if not record or interaction.user.id != self.user_id or record['step'] != 'rules':
            return await interaction.response.send_message("Cette étape n'est pas disponible pour toi.", ephemeral=True)
        set_step(record['guild_id'], record['user_id'], 'image')
        await interaction.response.send_message("**Étape 2/3 : capture du profil**\nEnvoie ici une capture complète de ton profil de jeu, avec ton pseudo clairement visible. Formats PNG, JPG ou WebP.")
        button.disabled = True
        await interaction.message.edit(view=self)


class Confirm(discord.ui.View):
    def __init__(self, user_id, name, message_id):
        super().__init__(timeout=None)
        self.user_id, self.name, self.message_id = user_id, name, message_id
        self.children[0].custom_id = f"vigile:confirm:{message_id}"
        self.children[1].custom_id = f"vigile:redo:{message_id}"

    async def interaction_check(self, interaction):
        record = session_channel(interaction.channel_id)
        if not record or record['user_id'] != interaction.user.id or record['step'] != 'confirm':
            await interaction.response.send_message("Confirmation non autorisée.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="C'est mon pseudo", style=discord.ButtonStyle.success, custom_id="vigile:confirm:placeholder")
    async def yes(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        record = session_channel(interaction.channel_id)
        try:
            original = await interaction.channel.fetch_message(self.message_id)
        except discord.HTTPException:
            return await interaction.followup.send("Capture introuvable, recommence.", ephemeral=True)
        await process_capture(original, confirmed_name=self.name)
        if DB.approved_user(record['guild_id'], record['user_id']):
            set_step(record['guild_id'], record['user_id'], 'done')
            asyncio.create_task(close_session(record['guild_id'], record['user_id']))
        elif DB.pending_for_user(record['guild_id'], record['user_id']):
            set_step(record['guild_id'], record['user_id'], 'pending')
        else:
            set_step(record['guild_id'], record['user_id'], 'image')
        for child in self.children: child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.followup.send("Confirmation prise en compte.", ephemeral=True)

    @discord.ui.button(label="Ce n'est pas mon pseudo", style=discord.ButtonStyle.secondary, custom_id="vigile:redo:placeholder")
    async def no(self, interaction, button):
        record = session_channel(interaction.channel_id)
        if record['attempts'] >= 2:
            await interaction.response.defer(ephemeral=True)
            try:
                original = await interaction.channel.fetch_message(self.message_id)
                data = await original.attachments[0].read()
                result = await asyncio.to_thread(extract_name, data, MIN_CONFIDENCE)
                await escalate_capture(original, data, result, 'Le joueur conteste le pseudo reconnu au deuxième essai')
            except (discord.HTTPException, ValueError):
                await interaction.followup.send('Impossible de transmettre la capture. Contacte le staff.', ephemeral=True)
                return
            for child in self.children: child.disabled = True
            await interaction.message.edit(view=self)
            await interaction.followup.send('Dossier transmis au staff.', ephemeral=True)
            return
        set_step(record['guild_id'], record['user_id'], 'image')
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content='Envoie ta deuxième capture plus nette et complète.', view=self)


async def publish_portals():
    for channel_id in sorted(VIGILE_CHANNELS):
        await ensure_portal(channel_id)


async def ensure_portal(channel_id: int) -> str:
    lock = portal_publish_locks.setdefault(channel_id, asyncio.Lock())
    async with lock:
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                log.error("Portail %s inaccessible : %r", channel_id, exc)
                return "Salon inaccessible : vérifie son ID et les permissions du bot."
        if not isinstance(channel, discord.TextChannel):
            log.error("Portail %s n'est pas un salon textuel.", channel_id)
            return "Le salon configuré n'est pas un salon textuel."
        existing = DB.db.execute(
            "SELECT message_id FROM portal_messages WHERE channel_id=?", (channel_id,)
        ).fetchone()
        if existing:
            try:
                old_portal = await channel.fetch_message(existing["message_id"])
                if old_portal.author.id == bot.user.id:
                    if old_portal.content != PORTAL_TEXT_FR or len(old_portal.components) < 2:
                        await old_portal.edit(content=PORTAL_TEXT_FR, view=Portal())
                        log.info("Portail mis à jour dans %s", channel_id)
                        return "Portail mis à jour."
                log.info("Portail existant dans %s : %s", channel_id, existing["message_id"])
                return "Portail déjà présent."
            except discord.NotFound:
                log.warning("Portail supprimé dans %s, republication.", channel_id)
            except discord.Forbidden:
                log.error("Permission Lire l'historique manquante dans %s", channel_id)
                return "Permission Lire l'historique des messages manquante."
            except discord.HTTPException as exc:
                log.error("Impossible de vérifier le portail %s : %r", channel_id, exc)
                return "Erreur Discord lors de la vérification du portail."
        try:
            msg = await channel.send(PORTAL_TEXT_FR, view=Portal())
            with DB.db:
                DB.db.execute(
                    "INSERT OR REPLACE INTO portal_messages(channel_id,message_id) VALUES (?,?)",
                    (channel_id, msg.id),
                )
            log.info("Portail publié dans %s : %s", channel_id, msg.id)
            return "Portail publié."
        except discord.Forbidden:
            log.error("Permission Envoyer des messages manquante dans %s", channel_id)
            return "Permission Envoyer des messages manquante."
        except discord.HTTPException as exc:
            log.exception("Publication du portail impossible dans %s : %r", channel_id, exc)
            return "Erreur Discord pendant la publication."


@tasks.loop(seconds=60)
async def portal_healthcheck():
    await publish_portals()


@portal_healthcheck.before_loop
async def before_portal_healthcheck():
    await bot.wait_until_ready()


@bot.tree.command(name="vigile-portail", description="Vérifier ou republier le portail de ce salon")
async def vigile_portail(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Commande réservée au serveur.", ephemeral=True)
        return
    if not staff(interaction.user):
        await interaction.response.send_message("Réservé aux modérateurs.", ephemeral=True)
        return
    if interaction.channel_id not in VIGILE_CHANNELS:
        await interaction.response.send_message("Commande utilisable dans Vigile et Vigile Test uniquement.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        result = await ensure_portal(interaction.channel_id)
    except Exception:
        log.exception("Erreur commande vigile-portail, salon %s", interaction.channel_id)
        result = "Erreur interne : consulte les logs Coolify."
    await interaction.followup.send(result, ephemeral=True)


async def repair_deleted_portal(channel_id: int, message_ids):
    """Recreate only the currently registered portal, after a short delay."""
    if channel_id not in VIGILE_CHANNELS:
        return
    row = DB.db.execute(
        "SELECT message_id FROM portal_messages WHERE channel_id=?", (channel_id,)
    ).fetchone()
    if not row or row["message_id"] not in message_ids:
        return
    task = portal_repair_tasks.get(channel_id)
    if task and not task.done():
        return

    async def restore():
        try:
            await asyncio.sleep(1)  # Attempt restoration promptly; Discord API latency may vary.
            current = DB.db.execute(
                "SELECT message_id FROM portal_messages WHERE channel_id=?", (channel_id,)
            ).fetchone()
            if current and current["message_id"] in message_ids:
                await ensure_portal(channel_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Échec de restauration du portail dans %s", channel_id)
        finally:
            portal_repair_tasks.pop(channel_id, None)

    portal_repair_tasks[channel_id] = asyncio.create_task(restore())


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    await repair_deleted_portal(payload.channel_id, {payload.message_id})


@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    await repair_deleted_portal(payload.channel_id, payload.message_ids)


def staff(member: discord.Member) -> bool:
    return (member.guild_permissions.manage_guild or member.guild_permissions.manage_channels) or (STAFF_ROLE != 0 and any(r.id == STAFF_ROLE for r in member.roles))


def role_for(guild: discord.Guild, origin_channel_id: int):
    return guild.get_role(ROLE_TEST if origin_channel_id == VIGILE_TEST else ROLE)


def duplicate_reason(guild: discord.Guild, user_id: int, name: str, digest: str):
    if DB.existing_name(guild.id, name, user_id):
        return "Pseudo déjà approuvé pour un autre membre"
    if DB.reused_image(guild.id, digest, user_id):
        return "Capture déjà validée pour un autre membre"
    # Vérification complémentaire des surnoms actifs (historique éventuellement incomplet).
    if any(m.id != user_id and (m.nick or "").casefold() == name.casefold() for m in guild.members):
        return "Surnom déjà utilisé sur le serveur"
    return None


async def apply_member(member: discord.Member, name: str, origin_channel_id: int):
    """Ne pas attribuer le rôle si le changement de surnom échoue."""
    role = role_for(member.guild, origin_channel_id)
    if role is None:
        raise RuntimeError("Rôle Membres introuvable")
    me = member.guild.me
    if not me or not me.guild_permissions.manage_nicknames or not me.guild_permissions.manage_roles:
        raise RuntimeError("Permissions Gérer les pseudos / Gérer les rôles manquantes")
    if me.top_role <= role or me.top_role <= member.top_role or member == member.guild.owner:
        raise RuntimeError("Hiérarchie des rôles Discord insuffisante")
    # Discord autorise l'attribution d'un rôle et la modification du surnom uniquement
    # si le rôle du bot est strictement supérieur à celui du membre.
    await member.edit(nick=name, reason="Vigile : capture de profil validée")
    await member.add_roles(role, reason="Vigile : capture de profil validée")


class ApproveName(discord.ui.Modal, title='Valider le pseudo du joueur'):
    nickname = discord.ui.TextInput(label='Pseudo exact du jeu', min_length=2, max_length=32, required=True)

    def __init__(self, case_id, proposed_name):
        super().__init__()
        self.case_id = case_id
        self.nickname.default = proposed_name[:32]

    async def on_submit(self, interaction):
        name = clean_name(str(self.nickname.value))
        if not name:
            return await interaction.response.send_message('Pseudo invalide.', ephemeral=True)
        case = DB.get(self.case_id)
        if not case or case['status'] != 'pending' or not isinstance(interaction.user, discord.Member) or not staff(interaction.user) or interaction.channel_id != alert_channel_for_case(case):
            return await interaction.response.send_message('Action non autorisée ou dossier déjà traité.', ephemeral=True)
        with DB.db:
            DB.db.execute('UPDATE cases SET proposed_name=? WHERE id=? AND status=\'pending\'', (name, self.case_id))
        await Review(self.case_id).decide(interaction, 'approve')


class Review(discord.ui.View):
    def __init__(self, case_id: int):
        super().__init__(timeout=None)
        self.case_id = case_id
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"vigile:{item.custom_id.split(':')[1]}:{case_id}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not staff(interaction.user) or interaction.channel_id != alert_channel_for_case(DB.get(self.case_id) or {"guild_id": interaction.guild_id, "user_id": 0}):
            await interaction.response.send_message("Action réservée aux modérateurs autorisés.", ephemeral=True)
            return False
        return True

    async def decide(self, interaction: discord.Interaction, action: str):
        await interaction.response.defer(ephemeral=True)
        lock = locks.setdefault(self.case_id, asyncio.Lock())
        async with lock:
            case = DB.get(self.case_id)
            if not case or case["status"] != "pending":
                await interaction.followup.send("Ce dossier a déjà été traité.", ephemeral=True)
                return
            guild = interaction.guild
            member = guild.get_member(case["user_id"]) if guild else None
            if member is None and guild is not None:
                try:
                    member = await guild.fetch_member(case["user_id"])
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    log.exception("Impossible de récupérer le membre %s", case["user_id"])
            if action == "approve":
                if member is None:
                    await interaction.followup.send("Membre introuvable : aucune modification effectuée.", ephemeral=True)
                    return
                name = clean_name(case["proposed_name"] or "")
                if not name:
                    await interaction.followup.send("Pseudo absent ou invalide. Demande une nouvelle capture.", ephemeral=True)
                    return
                problem = duplicate_reason(guild, member.id, name, case["image_sha256"])
                if problem:
                    await interaction.followup.send(f"Validation bloquée : {problem}.", ephemeral=True)
                    return
                try:
                    await apply_member(member, name, case["origin_channel_id"])
                except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                    log.warning("Validation échouée pour %s : %s", member.id, exc)
                    await interaction.followup.send(f"Échec Discord : {exc}. Vérifie les permissions et réessaie.", ephemeral=True)
                    return
                DB.approve(self.case_id, name, interaction.user.id)
                set_step(guild.id, member.id, "done")
                asyncio.create_task(close_session(guild.id, member.id))
                outcome = f"Validé par {interaction.user.mention} : {discord.utils.escape_markdown(name)}"
                try:
                    await member.send(f"✅ Profil validé : ton surnom est **{discord.utils.escape_markdown(name)}** et le rôle Membres a été attribué.")
                except discord.HTTPException:
                    pass
            else:
                DB.set_status(self.case_id, "rejected" if action == "reject" else "retry", interaction.user.id)
                if member and action == "reject":
                    set_step(guild.id, member.id, "rejected")
                    asyncio.create_task(close_session(guild.id, member.id))
                elif member and action == "retry":
                    set_step(guild.id, member.id, "image")
                outcome = ("Refusé" if action == "reject" else "Nouvelle capture demandée") + f" par {interaction.user.mention}"
                if member:
                    try:
                        await member.send("❌ Ta capture a été refusée." if action == "reject" else "📸 Merci de renvoyer une capture complète et nette de ton profil dans le salon Vigile.")
                    except discord.HTTPException:
                        pass
            for child in self.children:
                child.disabled = True
            # Supprimer l'alerte uniquement après une décision enregistrée.
            try:
                if interaction.message:
                    await interaction.message.delete()
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Suppression de l'alerte %s impossible", self.case_id)
                try:
                    if interaction.message:
                        await interaction.message.edit(content=f"**Dossier #{self.case_id} — {outcome}**", embed=None, attachments=[], view=self)
                except discord.HTTPException:
                    pass
            await interaction.followup.send(outcome, ephemeral=True)

    @discord.ui.button(label="Valider", style=discord.ButtonStyle.success, custom_id="vigile:approve:placeholder")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        case = DB.get(self.case_id)
        if not case or case['status'] != 'pending':
            return await interaction.response.send_message('Dossier déjà traité.', ephemeral=True)
        await interaction.response.send_modal(ApproveName(self.case_id, case['proposed_name'] or ''))

    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger, custom_id="vigile:reject:placeholder")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.decide(interaction, "reject")



async def mod_alert(case_id: int, user: discord.Member, name: str | None, confidence: float, reason: str, image: bytes):
    case = DB.get(case_id)
    target = alert_channel_for_case(case)
    channel = bot.get_channel(target) or await bot.fetch_channel(target)
    embed = discord.Embed(title="🛡️ Vigile — vérification nécessaire", color=discord.Color.orange())
    embed.add_field(name="Joueur Discord", value=f"{user.mention} (`{user.id}`)", inline=False)
    embed.add_field(name="Pseudo proposé", value=discord.utils.escape_markdown(name or "Non reconnu"), inline=True)
    embed.add_field(name="Confiance OCR", value=f"{confidence:.1f}%", inline=True)
    embed.add_field(name="Anomalie", value=reason, inline=False)
    embed.set_footer(text=f"Dossier #{case_id} • Capture à examiner avant validation")
    session = session_for(case['guild_id'], case['user_id'])
    if session:
        embed.add_field(name='Capture 1', value=session['first_image_url'] or 'Indisponible', inline=False)
        embed.add_field(name='Capture 2', value=session['second_image_url'] or 'Indisponible', inline=False)
    if image:
        file = discord.File(io.BytesIO(image), filename=f'capture_{case_id}.png')
        embed.set_image(url=f'attachment://capture_{case_id}.png')
        await channel.send(embed=embed, file=file, view=Review(case_id), allowed_mentions=discord.AllowedMentions.none())
    else:
        await channel.send(embed=embed, view=Review(case_id), allowed_mentions=discord.AllowedMentions.none())


@bot.event
async def on_ready():
    log.info("Connecté en tant que %s (%s)", bot.user, bot.user.id)
    log.info("Salons Vigile surveillés : %s", sorted(VIGILE_CHANNELS))
    await publish_portals()
    if not portal_healthcheck.is_running():
        portal_healthcheck.start()
    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            log.info("Commandes synchronisées sur %s : %s", guild.id, [c.name for c in synced])
        except discord.HTTPException:
            log.exception("Synchronisation des commandes impossible pour %s", guild.id)
    for guild in bot.guilds:
        log.info("Serveur %s (%s), rôle principal=%s, rôle test=%s", guild.name, guild.id, bool(guild.get_role(ROLE)), bool(guild.get_role(ROLE_TEST)))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    record = session_channel(message.channel.id)
    if not record or record['user_id'] != message.author.id or record['step'] != 'image':
        return
    attachments = [a for a in message.attachments if
                   (a.content_type or '').lower().split(';')[0] in ('image/png', 'image/jpeg', 'image/webp')]
    if not attachments:
        await message.reply('📸 Please send a PNG, JPG or WebP screenshot of your profile.' if record['language'] == 'en' else '📸 Envoie une capture PNG, JPG ou WebP de ton profil.', mention_author=False)
        return
    if attachments[0].size > MAX_BYTES:
        await message.reply('⚠️ Image too large (8 MB maximum).' if record['language'] == 'en' else '⚠️ Image trop volumineuse (8 Mo maximum).', mention_author=False)
        return
    # Verrou par joueur : deux images envoyées simultanément ne créent pas deux dossiers.
    lock = locks.setdefault(('upload', message.guild.id, message.author.id), asyncio.Lock())
    async with lock:
        record = session_channel(message.channel.id)
        if not record or record['step'] != 'image':
            return
        attempt = increment_attempt(message.guild.id, message.author.id, attachments[0].url)
        try:
            data = await attachments[0].read()
            result = await asyncio.to_thread(extract_name, data, MIN_CONFIDENCE)
        except (discord.HTTPException, ValueError) as exc:
            result = None
            reason = f'Image impossible à lire : {type(exc).__name__}'
        else:
            reason = result.reason
        if result and result.name and result.reason == 'OK':
            reason = duplicate_reason(message.guild, message.author.id, result.name, hashlib.sha256(data).hexdigest())
            if reason is None:
                set_step(message.guild.id, message.author.id, 'confirm')
                await message.reply(
                    f'**Étape 3/3 — Essai {attempt}/2**\nPseudo détecté : **{discord.utils.escape_markdown(result.name)}**. Est-ce bien ton pseudo ?',
                    view=Confirm(message.author.id, result.name, message.id),
                    mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                return
        if retry_or_escalate(message.guild.id, message.author.id, reason):
            await message.reply(
                f'⚠️ Première tentative non validée : {reason}.\n'
                '**Tu as droit à un deuxième essai.** Envoie une nouvelle capture avec le pseudo bien visible.',
                mention_author=False)
            return
        await escalate_capture(message, data if result else b'', result, reason)


async def escalate_capture(message, data, result, reason):
    user = message.author
    attachment = message.attachments[0]
    digest = hashlib.sha256(data).hexdigest() if data else hashlib.sha256(attachment.url.encode()).hexdigest()
    case_id = DB.create_case(message.guild.id, user.id, message.id, digest, attachment.url,
                             result.name if result else None, result.confidence if result else 0, reason,
                             session_for(message.guild.id, user.id)["origin_channel_id"])
    if case_id is None:
        return
    try:
        await mod_alert(case_id, user, result.name if result else None,
                        result.confidence if result else 0, reason, data)
        await message.reply('🛡️ Both attempts failed. Your case has been sent to the moderators. Please wait for their decision.' if record['language'] == 'en' else '🛡️ Deux essais infructueux. Ton dossier a été transmis aux modérateurs. Attends leur décision.', mention_author=False)
    except (discord.HTTPException, discord.Forbidden):
        log.exception('Alerte modo impossible pour dossier %s', case_id)
        await message.reply('⚠️ Could not contact moderators automatically. Please contact an administrator.' if record['language'] == 'en' else '⚠️ Impossible de contacter le staff automatiquement. Contacte un administrateur.', mention_author=False)


async def process_capture(message: discord.Message, confirmed_name=None):
    # Une confirmation n'est pas une nouvelle tentative : la capture a déjà été comptée.
    user = message.author
    record = session_for(message.guild.id, user.id)
    if not record or DB.approved_user(message.guild.id, user.id) or DB.pending_for_user(message.guild.id, user.id):
        return
    try:
        data = await message.attachments[0].read()
        result = await asyncio.to_thread(extract_name, data, MIN_CONFIDENCE)
    except (discord.HTTPException, ValueError) as exc:
        data, result = b'', None
        reason = f'Erreur de lecture : {type(exc).__name__}'
    else:
        reason = result.reason if result.reason != 'OK' else duplicate_reason(
            message.guild, user.id, result.name, hashlib.sha256(data).hexdigest())
        if confirmed_name is not None and result.name != confirmed_name:
            reason = 'Pseudo différent de celui confirmé'
    if reason is None and result:
        try:
            await apply_member(user, result.name, record["origin_channel_id"])
        except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
            reason = f'Erreur de configuration Discord : {exc}'
        else:
            digest = hashlib.sha256(data).hexdigest()
            case_id = DB.create_case(message.guild.id, user.id, message.id, digest,
                                     message.attachments[0].url, result.name, result.confidence, 'Validation automatique',
                                     record['origin_channel_id'])
            if case_id is not None:
                DB.approve(case_id, result.name)
            await message.reply(f'✅ Bienvenue **{discord.utils.escape_markdown(result.name)}** ! Rôle Membres attribué. Fermeture dans 10 secondes.', mention_author=False)
            set_step(message.guild.id, user.id, 'done')
            asyncio.create_task(close_session(message.guild.id, user.id))
            return
    if retry_or_escalate(message.guild.id, user.id, reason):
        await message.reply(f'⚠️ {reason}. Tu peux envoyer une deuxième capture.', mention_author=False)
    else:
        await escalate_capture(message, data, result, reason)


@bot.tree.command(name="vigile-reset", description="Réinitialiser uniquement les vérifications du portail de test")
@app_commands.default_permissions(manage_guild=True, manage_channels=True)
async def vigile_reset(interaction: discord.Interaction):
    # Les commandes slash ne sont pas masquables dynamiquement par salon ; refus strict ailleurs.
    if not interaction.guild or interaction.channel_id != VIGILE_TEST:
        return await interaction.response.send_message(
            "Commande autorisée uniquement dans le salon Vigile Test.", ephemeral=True)
    if not isinstance(interaction.user, discord.Member) or not staff(interaction.user):
        return await interaction.response.send_message("Réservé au staff autorisé.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    approved, pending, channels = DB.reset_test(interaction.guild.id, VIGILE_TEST, interaction.user.id)
    failed = 0
    for channel_id in channels:
        channel = interaction.guild.get_channel(channel_id)
        if channel:
            try:
                await channel.delete(reason=f"Vigile IA : reset test par {interaction.user.id}")
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
                log.exception("Impossible de supprimer le salon test %s", channel_id)
    await interaction.followup.send(
        f"✅ Tests réinitialisés : {approved} validation(s), {pending} dossier(s) en attente, "
        f"{len(channels)-failed} salon(s) fermé(s). "
        "Les rôles Discord ne sont pas retirés. Les validations historiques sans origine connue "
        "et les dossiers officiels sont conservés." +
        (f" ⚠️ {failed} salon(s) n'ont pas pu être supprimés." if failed else ""), ephemeral=True)


@bot.event
async def setup_hook():
    # Restaurer les boutons des dossiers en attente après un redémarrage.
    await bot.tree.sync()
    bot.add_view(Portal())
    for case in DB.db.execute("SELECT id FROM cases WHERE status='pending'").fetchall():
        bot.add_view(Review(case["id"]))
    for session in DB.db.execute("SELECT user_id,channel_id,step,language FROM sessions").fetchall():
        if session['step'] == 'rules':
            bot.add_view(Rules(session['user_id'], session['language']))
    # Les confirmations actives sont restaurées depuis les messages persistés.
    for session in DB.db.execute("SELECT channel_id,user_id FROM sessions WHERE step='confirm'").fetchall():
        channel = bot.get_channel(session['channel_id'])
        if channel:
            try:
                async for msg in channel.history(limit=15):
                    if msg.author.id == bot.user.id and msg.components and 'Pseudo détecté' in msg.content:
                        # L'identifiant de capture est extrait du custom_id de la confirmation.
                        cid = msg.components[0].children[0].custom_id
                        source_id = int(cid.rsplit(':', 1)[-1])
                        original = await channel.fetch_message(source_id)
                        result = await asyncio.to_thread(extract_name, await original.attachments[0].read(), MIN_CONFIDENCE)
                        if result.name:
                            bot.add_view(Confirm(session['user_id'], result.name, source_id))
                        break
            except (discord.HTTPException, IndexError, ValueError, AttributeError):
                log.warning("Confirmation non restaurée dans %s", session['channel_id'])


if __name__ == "__main__":
    if not TOKEN or TOKEN == "COLLER_TOKEN_BOT_ICI":
        raise SystemExit("Renseigner DISCORD_TOKEN dans les variables d'environnement")
    bot.run(TOKEN, log_handler=None)
