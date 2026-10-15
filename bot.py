"""Vigile IA: vérification des captures de profil Discord.
La capture n'est PAS une preuve d'identité ou de propriété du compte de jeu.
"""
import asyncio
import hashlib
import io
import logging
import os

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
    for guild in bot.guilds:
        log.info("Serveur %s (%s), rôle %s", guild.name, guild.id, bool(guild.get_role(ROLE)))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.channel.id not in VIGILE_CHANNELS or not message.guild:
        return
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
    for case in DB.db.execute("SELECT id FROM cases WHERE status='pending'").fetchall():
        bot.add_view(Review(case["id"]))


if __name__ == "__main__":
    if not TOKEN or TOKEN == "COLLER_TOKEN_BOT_ICI":
        raise SystemExit("Renseigner DISCORD_TOKEN dans les variables d'environnement")
    bot.run(TOKEN, log_handler=None)
