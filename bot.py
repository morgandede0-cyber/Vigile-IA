"""Vigile IA: vérification des captures de profil Discord.
La capture n'est PAS une preuve d'identité ou de propriété du compte de jeu.
"""
import asyncio
import hashlib
import io
import logging
import os
import sqlite3

import discord
from discord.ext import commands
from dotenv import load_dotenv

from ocr import clean_name, extract_name
from storage import Storage

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vigile")

TOKEN = os.getenv("DISCORD_TOKEN", "")
VIGILE_CHANNELS = {int(x.strip()) for x in os.getenv("VIGILE_CHANNEL_IDS", "1522707744828821765,1560371205939396759").split(",") if x.strip()}
MOD = int(os.getenv("MOD_CHANNEL_ID", "1531616175627173928"))
ROLE = int(os.getenv("MEMBER_ROLE_ID", "1522696283595472916"))
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

# Portail public, salons de vérification privés persistants.
DB.db.executescript("""
CREATE TABLE IF NOT EXISTS portal_messages (channel_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 channel_id INTEGER NOT NULL UNIQUE, step TEXT NOT NULL,
 PRIMARY KEY(guild_id, user_id)
);
""")
DB.db.commit()


def session_for(guild_id, user_id):
    return DB.db.execute("SELECT * FROM sessions WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()


def session_channel(channel_id):
    return DB.db.execute("SELECT * FROM sessions WHERE channel_id=?", (channel_id,)).fetchone()


def set_step(guild_id, user_id, step):
    with DB.db:
        DB.db.execute("UPDATE sessions SET step=? WHERE guild_id=? AND user_id=?", (step, guild_id, user_id))


class Portal(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Commencer ma vérification", style=discord.ButtonStyle.success, emoji="🛡️", custom_id="vigile:portal:start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
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
                return await interaction.followup.send(f"Ta vérification est déjà ouverte : {channel.mention}", ephemeral=True)
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
                name=f"verification-{member.id}",
                category=interaction.channel.category if isinstance(interaction.channel, discord.TextChannel) else None,
                overwrites=overwrites,
                topic=f"Vigile IA - vérification privée du membre {member.id}",
                reason="Vigile IA : création du parcours privé"
            )
        except discord.HTTPException as exc:
            log.exception("Création du salon privé impossible")
            return await interaction.followup.send(f"Impossible d'ouvrir la vérification : {exc}", ephemeral=True)
        with DB.db:
            DB.db.execute("INSERT INTO sessions(guild_id,user_id,channel_id,step) VALUES (?,?,?,?)", (guild.id, member.id, channel.id, 'rules'))
        await channel.send(f"Bienvenue {member.mention} ! **Étape 1/3 : règlement**\nRespecte les règles du serveur et les autres membres. N'envoie pas de captures appartenant à autrui. Clique sur le bouton pour accepter et continuer.", view=Rules(member.id))
        await interaction.followup.send(f"Ton espace privé est prêt : {channel.mention}", ephemeral=True)


class Rules(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id
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
        set_step(record['guild_id'], record['user_id'], 'image')
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content="Envoie une nouvelle capture plus nette et complète.", view=self)


async def publish_portals():
    for channel_id in VIGILE_CHANNELS:
        channel = bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("Salon portail inaccessible : %s", channel_id)
            continue
        existing = DB.db.execute("SELECT message_id FROM portal_messages WHERE channel_id=?", (channel_id,)).fetchone()
        if existing:
            try:
                await channel.fetch_message(existing['message_id'])
                continue
            except discord.NotFound:
                pass
            except discord.Forbidden:
                log.warning("Lecture du portail interdite dans %s", channel_id)
                continue
        try:
            msg = await channel.send("# 🛡️ VIGILE IA — PORTAIL D'ACCUEIL\nBienvenue ! Pour obtenir le rôle **Membres**, clique ci-dessous.\n\nTon inscription se déroulera dans **un salon privé** : règlement → capture du profil → confirmation du pseudo → accès au serveur.\nEn cas d'anomalie, un modérateur sera prévenu.", view=Portal())
            with DB.db:
                DB.db.execute("INSERT OR REPLACE INTO portal_messages(channel_id,message_id) VALUES (?,?)", (channel_id,msg.id))
        except discord.HTTPException:
            log.exception("Publication du portail impossible dans %s", channel_id)



def staff(member: discord.Member) -> bool:
    return member.guild_permissions.manage_guild or (STAFF_ROLE != 0 and any(r.id == STAFF_ROLE for r in member.roles))


def role_for(guild: discord.Guild):
    return guild.get_role(ROLE)


def duplicate_reason(guild: discord.Guild, user_id: int, name: str, digest: str):
    if DB.existing_name(guild.id, name, user_id):
        return "Pseudo déjà approuvé pour un autre membre"
    if DB.reused_image(guild.id, digest, user_id):
        return "Capture déjà validée pour un autre membre"
    # Vérification complémentaire des surnoms actifs (historique éventuellement incomplet).
    if any(m.id != user_id and (m.nick or "").casefold() == name.casefold() for m in guild.members):
        return "Surnom déjà utilisé sur le serveur"
    return None


async def apply_member(member: discord.Member, name: str):
    """Ne pas attribuer le rôle si le changement de surnom échoue."""
    role = role_for(member.guild)
    if role is None:
        raise RuntimeError("Rôle Membres introuvable")
    me = member.guild.me
    if not me or not me.guild_permissions.manage_nicknames or not me.guild_permissions.manage_roles:
        raise RuntimeError("Permissions Gérer les pseudos / Gérer les rôles manquantes")
    if me.top_role <= role or me.top_role <= member.top_role or member == member.guild.owner:
        raise RuntimeError("Hiérarchie des rôles Discord insuffisante")
    await member.edit(nick=name, reason="Vigile : capture de profil validée")
    await member.add_roles(role, reason="Vigile : capture de profil validée")


class Review(discord.ui.View):
    def __init__(self, case_id: int):
        super().__init__(timeout=None)
        self.case_id = case_id
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"vigile:{item.custom_id.split(':')[1]}:{case_id}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not staff(interaction.user) or interaction.channel_id != MOD:
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
                    await apply_member(member, name)
                except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                    log.warning("Validation échouée pour %s : %s", member.id, exc)
                    await interaction.followup.send(f"Échec Discord : {exc}. Vérifie les permissions et réessaie.", ephemeral=True)
                    return
                DB.approve(self.case_id, name, interaction.user.id)
                outcome = f"Validé par {interaction.user.mention} : {discord.utils.escape_markdown(name)}"
                try:
                    await member.send(f"✅ Profil validé : ton surnom est **{discord.utils.escape_markdown(name)}** et le rôle Membres a été attribué.")
                except discord.HTTPException:
                    pass
            else:
                DB.set_status(self.case_id, "rejected" if action == "reject" else "retry", interaction.user.id)
                outcome = ("Refusé" if action == "reject" else "Nouvelle capture demandée") + f" par {interaction.user.mention}"
                if member:
                    try:
                        await member.send("❌ Ta capture a été refusée." if action == "reject" else "📸 Merci de renvoyer une capture complète et nette de ton profil dans le salon Vigile.")
                    except discord.HTTPException:
                        pass
            for child in self.children:
                child.disabled = True
            try:
                await interaction.message.edit(content=f"**Dossier #{self.case_id} — {outcome}**", view=self)
            except discord.HTTPException:
                pass
            await interaction.followup.send(outcome, ephemeral=True)

    @discord.ui.button(label="Valider", style=discord.ButtonStyle.success, custom_id="vigile:approve:placeholder")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.decide(interaction, "approve")

    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger, custom_id="vigile:reject:placeholder")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.decide(interaction, "reject")

    @discord.ui.button(label="Nouvelle capture", style=discord.ButtonStyle.secondary, custom_id="vigile:retry:placeholder")
    async def retry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.decide(interaction, "retry")


async def mod_alert(case_id: int, user: discord.Member, name: str | None, confidence: float, reason: str, image: bytes):
    channel = bot.get_channel(MOD) or await bot.fetch_channel(MOD)
    embed = discord.Embed(title="🛡️ Vigile — vérification nécessaire", color=discord.Color.orange())
    embed.add_field(name="Joueur Discord", value=f"{user.mention} (`{user.id}`)", inline=False)
    embed.add_field(name="Pseudo proposé", value=discord.utils.escape_markdown(name or "Non reconnu"), inline=True)
    embed.add_field(name="Confiance OCR", value=f"{confidence:.1f}%", inline=True)
    embed.add_field(name="Anomalie", value=reason, inline=False)
    embed.set_footer(text=f"Dossier #{case_id} • Capture à examiner avant validation")
    file = discord.File(io.BytesIO(image), filename=f"capture_{case_id}.png")
    embed.set_image(url=f"attachment://capture_{case_id}.png")
    await channel.send(embed=embed, file=file, view=Review(case_id), allowed_mentions=discord.AllowedMentions.none())


@bot.event
async def on_ready():
    log.info("Connecté en tant que %s (%s)", bot.user, bot.user.id)
    log.info("Salons Vigile surveillés : %s", sorted(VIGILE_CHANNELS))
    await publish_portals()
    for guild in bot.guilds:
        log.info("Serveur %s (%s), rôle %s", guild.name, guild.id, bool(guild.get_role(ROLE)))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    record = session_channel(message.channel.id)
    if not record or record['user_id'] != message.author.id:
        return
    if record['step'] != 'image':
        return
    attachments = [a for a in message.attachments if a.content_type and a.content_type.lower().split(';')[0] in ("image/png", "image/jpeg", "image/webp")]
    if not attachments:
        await message.reply("📸 Envoie une image de ton profil pour continuer.", mention_author=False)
        return
    if attachments[0].size > MAX_BYTES:
        await message.reply("Image trop volumineuse (8 Mo maximum).", mention_author=False)
        return
    try:
        data = await attachments[0].read()
        result = await asyncio.to_thread(extract_name, data, MIN_CONFIDENCE)
    except (discord.HTTPException, ValueError) as exc:
        await message.reply(f"Image illisible : {exc}", mention_author=False)
        return
    if not result.name or result.reason != "OK":
        await process_capture(message)
        if DB.pending_for_user(message.guild.id, message.author.id):
            set_step(message.guild.id, message.author.id, 'pending')
        return
    set_step(message.guild.id, message.author.id, 'confirm')
    await message.reply(f"**Étape 3/3 : confirmation**\nPseudo détecté : **{discord.utils.escape_markdown(result.name)}**. Est-ce bien le tien ?", view=Confirm(message.author.id, result.name, message.id), mention_author=False, allowed_mentions=discord.AllowedMentions.none())


async def process_capture(message: discord.Message, confirmed_name=None):

    if not isinstance(message.author, discord.Member):
        return
    user = message.author
    if DB.approved_user(message.guild.id, user.id):
        await message.reply("✅ Ton profil a déjà été validé.", mention_author=False)
        return
    if DB.pending_for_user(message.guild.id, user.id):
        await message.reply("⏳ Ta précédente capture attend une décision du staff.", mention_author=False)
        return
    attachments = [a for a in message.attachments if a.content_type and a.content_type.lower().split(';')[0] in ("image/png", "image/jpeg", "image/webp")]
    if not attachments:
        await message.reply("📸 Envoie une capture complète de l'écran **Informations joueur**, avec ton pseudo visible dans la section Profil.", mention_author=False)
        return
    attachment = attachments[0]
    if attachment.size > MAX_BYTES:
        await message.reply("⚠️ Image trop volumineuse. Envoie une capture de moins de 8 Mo.", mention_author=False)
        return
    try:
        data = await attachment.read()
        if len(data) > MAX_BYTES:
            raise ValueError("Image trop volumineuse")
        result = await asyncio.to_thread(extract_name, data, MIN_CONFIDENCE)
    except (discord.HTTPException, ValueError) as exc:
        await message.reply(f"⚠️ Impossible de lire cette image : {exc}", mention_author=False)
        return
    digest = hashlib.sha256(data).hexdigest()
    reason = result.reason if result.reason != "OK" else duplicate_reason(message.guild, user.id, result.name, digest)
    if confirmed_name is not None and result.name != confirmed_name:
        reason = "Le pseudo reconnu a changé entre la lecture et la confirmation"
    # Même en cas de confiance élevée, une capture ne prouve pas la propriété du compte.
    # Ici la validation automatique est limitée aux captures conformes sans conflit connu.
    if reason is None:
        try:
            await apply_member(user, result.name)
        except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
            reason = f"Échec de configuration Discord : {exc}"
        else:
            case_id = DB.create_case(message.guild.id, user.id, message.id, digest, attachment.url, result.name, result.confidence, "Validation automatique")
            if case_id is not None:
                DB.approve(case_id, result.name)
            await message.reply(f"✅ Bienvenue {discord.utils.escape_markdown(result.name)} ! Ton surnom et le rôle **Membres** ont été attribués.", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            return
    case_id = DB.create_case(message.guild.id, user.id, message.id, digest, attachment.url, result.name, result.confidence, reason or "Vérification requise")
    if case_id is None:
        return
    try:
        await mod_alert(case_id, user, result.name, result.confidence, reason, data)
        await message.reply("🛡️ Ta capture nécessite une vérification. Un modérateur a été prévenu ; aucun rôle n'a été attribué.", mention_author=False)
    except discord.HTTPException:
        log.exception("Impossible d'envoyer l'alerte au salon Modo pour dossier %s", case_id)
        await message.reply("⚠️ Vérification suspendue : le staff n'a pas pu être contacté. Contacte un administrateur.", mention_author=False)


@bot.event
async def setup_hook():
    # Restaurer les boutons des dossiers en attente après un redémarrage.
    bot.add_view(Portal())
    for case in DB.db.execute("SELECT id FROM cases WHERE status='pending'").fetchall():
        bot.add_view(Review(case["id"]))
    for session in DB.db.execute("SELECT user_id,channel_id,step FROM sessions").fetchall():
        if session['step'] == 'rules':
            bot.add_view(Rules(session['user_id']))
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
