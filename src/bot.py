"""Bot Telegram de Kairos (long polling, aucune exposition réseau requise).

L'interactivité repose sur des **boutons inline** (callback queries) : dans un
groupe, ils sont toujours transmis au bot et cliquables par tous les membres,
contrairement à une reply keyboard (bloquée par le mode confidentialité).
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)
from telegram.request import HTTPXRequest

from .config import Settings
from .net import ipv4_transport
from .service import KairosService, esc

logger = logging.getLogger(__name__)

LIST_PAGE_SIZE = 6
MAX_MSG = 3900

HELP_TEXT = (
    "🎢 <b>Kairos</b> — votre copilote à PortAventura World.\n\n"
    "Ouvre le panneau : /menu\n\n"
    "<b>Commandes</b>\n"
    "/ou — où aller maintenant (top 3)\n"
    "/etat [parc] — temps d'attente en direct\n"
    "/zone &lt;nom&gt; — indiquer votre zone actuelle\n"
    "/liste — toutes les attractions et priorités\n"
    "/fait &lt;attraction&gt; / /annule &lt;attraction&gt;\n"
    "/ajouter &lt;attraction&gt; / /retirer &lt;attraction&gt;\n"
    "/prio &lt;attraction&gt; &lt;1-3&gt; — régler la priorité\n"
    "/seuil [minutes] — voir/régler le seuil d'alerte\n"
    "/alertes [toutes|coasters] — portée des notifications\n"
    "/masquer &lt;attraction&gt; — retirer des listes/recommandations\n"
    "/afficher &lt;attraction&gt; — réafficher une attraction masquée\n"
    "/parc — occupation par parc\n"
    "/mode collect|trip — mode de fonctionnement\n"
    "/refresh — forcer une mise à jour\n"
    "/stats — état du service\n"
    "/test — envoyer une notification de test\n"
    "/id — identifiant de ce chat\n"
)


# --------------------------------------------------------------- utilitaires --- #


def esc_text(text: str) -> str:
    return esc(text)


def split_message(text: str, limit: int = MAX_MSG) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def _fit(text: str, limit: int = MAX_MSG) -> str:
    return text if len(text) <= limit else text[: limit - 20] + "\n<i>…tronqué</i>"


async def _reply(update: Update, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    if update.effective_chat is None:
        return
    for chunk in split_message(text):
        await update.effective_chat.send_message(chunk, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _safe_edit(query, text: str, keyboard: InlineKeyboardMarkup | None) -> None:
    try:
        await query.edit_message_text(_fit(text), parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


def _btn(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)


def _back_menu() -> InlineKeyboardButton:
    return _btn("◀️ Menu", "menu:main")


# ------------------------------------------------------------------ panneaux --- #


def kb_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_btn("🎯 Où aller", "menu:reco"), _btn("📊 Temps d'attente", "menu:etat")],
            [_btn("📍 Ma zone", "menu:zone"), _btn("✅ Checklist", "menu:liste:0")],
            [_btn("⚙️ Réglages", "menu:reglages"), _btn("ℹ️ Aide", "menu:aide")],
            [_btn("♻️ Actualiser", "menu:refresh")],
        ]
    )


def panel_menu(service: KairosService) -> tuple[str, InlineKeyboardMarkup]:
    zone = service.current_zone() or "inconnue"
    recos = service.engine.recommend(service.current_zone(), limit=1)
    if recos:
        r = recos[0]
        wait = f"{r.wait_minutes} min" if r.wait_minutes is not None else "?"
        tip = f"Meilleur choix : <b>{esc_text(r.ride.name)}</b> — {wait} · {esc_text(r.ride.zone)}"
    else:
        tip = "Aucune attraction ouverte actuellement."
    text = (
        "🎢 <b>Kairos</b> — panneau de bord\n"
        f"Phase : <b>{service.phase_label()}</b> · Zone : <b>{esc_text(zone)}</b>\n\n{tip}"
    )
    return text, kb_menu()


def kb_reco(service: KairosService) -> InlineKeyboardMarkup:
    recos = service.engine.recommend(service.current_zone(), limit=3)
    rows = [[_btn(f"✅ {r.ride.name}", f"done:{r.ride.id}")] for r in recos]
    rows.append([_btn("♻️ Actualiser", "menu:refresh"), _btn("🔄 Autres", "menu:reco")])
    rows.append([_back_menu()])
    return InlineKeyboardMarkup(rows)


def panel_reco(service: KairosService) -> tuple[str, InlineKeyboardMarkup]:
    return service.format_recos(3), kb_reco(service)


def kb_etat(service: KairosService, park: str | None) -> InlineKeyboardMarkup:
    rows = [[_btn("🏟️ Tous les parcs", "menu:etat")]]
    for p in service.parks:
        mark = "• " if park != p["key"] else "▸ "
        rows.append([_btn(f"{mark}{p['name']}", f"menu:etat:{p['key']}")])
    rows.append([_back_menu()])
    return InlineKeyboardMarkup(rows)


def panel_etat(service: KairosService, park: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    title = service.park_name(park) if park else "tous les parcs"
    text = f"📊 <b>Temps d'attente</b> — {esc_text(title)}\n\n{service.format_status_table(park)}"
    return text, kb_etat(service, park)


def kb_zones(service: KairosService) -> InlineKeyboardMarkup:
    zones = service.zones()
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, zone in enumerate(zones):
        row.append(_btn(zone, f"zone:set:{index}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_back_menu()])
    return InlineKeyboardMarkup(rows)


def panel_zone(service: KairosService) -> tuple[str, InlineKeyboardMarkup]:
    current = service.current_zone()
    text = "📍 <b>Où es-tu ?</b>\nChoisis ta zone pour affiner les temps de marche."
    if current:
        text += f"\n\nZone actuelle : <b>{esc_text(current)}</b>"
    return text, kb_zones(service)


def panel_liste(service: KairosService, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    items = sorted(service.rides(), key=lambda r: (r.park, r.zone, r.name))
    total_pages = max(1, (len(items) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * LIST_PAGE_SIZE : (page + 1) * LIST_PAGE_SIZE]
    done = service.store.done_ids()

    rows: list[list[InlineKeyboardButton]] = []
    for ride in chunk:
        label = f"↩️ {ride.name}" if ride.id in done else f"✅ {ride.name}"
        rows.append([_btn(label, f"toggle:{ride.id}:{page}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("⬅️", f"menu:liste:{page - 1}"))
    nav.append(_btn(f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(_btn("➡️", f"menu:liste:{page + 1}"))
    rows.append(nav)
    rows.append([_back_menu()])

    text = (
        f"✅ <b>Checklist</b> — {len(done)}/{len(items)} faites\n"
        "<i>Touche une attraction pour la cocher ou la décocher.</i>"
    )
    return text, InlineKeyboardMarkup(rows)


def panel_reglages(service: KairosService) -> tuple[str, InlineKeyboardMarkup]:
    thr = service.alerter.default_threshold()
    trip = service.mode == "trip"
    coasters_only = service.alerter.alert_scope() == "coasters"
    text = (
        "⚙️ <b>Réglages</b>\n\n"
        f"🔔 Seuil d'alerte : <b>{thr} min</b>\n"
        "<i>Le bot prévient quand une attraction passe sous ce temps.</i>\n\n"
        f"Portée des alertes : <b>{'montagnes russes' if coasters_only else 'toutes attractions'}</b>\n"
        f"Mode : <b>{'séjour (alertes actives)' if trip else 'collecte (sans alertes)'}</b>"
    )
    rows = [
        [
            _btn("➖5", "seuil:-5"),
            _btn("➖1", "seuil:-1"),
            _btn(f"{thr} min", "noop"),
            _btn("➕1", "seuil:1"),
            _btn("➕5", "seuil:5"),
        ],
        [
            _btn(
                "🟡 Passer en mode séjour" if not trip else "🟢 Repasser en mode collecte",
                "mode:toggle",
            )
        ],
        [
            _btn(
                "🎢 Alertes : coasters" if not coasters_only else "🔔 Alertes : toutes",
                "alertes:toggle",
            )
        ],
        [_btn("🔔 Notification de test", "test:notify"), _btn("♻️ Actualiser", "menu:refresh")],
        [_back_menu()],
    ]
    return text, InlineKeyboardMarkup(rows)


# ----------------------------------------------------------------- handlers --- #


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    await _reply(update, *panel_menu(service))


async def cmd_aide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP_TEXT, InlineKeyboardMarkup([[_back_menu()]]))


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    await _reply(update, *panel_menu(service))


async def cmd_ou(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    await _reply(update, *panel_reco(service))


async def cmd_etat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    park_key = None
    if context.args:
        query = " ".join(context.args).lower()
        for key in service.parks_by_key:
            if query in key or query in service.park_name(key).lower():
                park_key = key
                break
    await _reply(update, *panel_etat(service, park_key))


async def cmd_zone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    if not context.args:
        await _reply(update, *panel_zone(service))
        return
    zone = service.resolve_zone(" ".join(context.args))
    if zone is None:
        await _reply(update, *panel_zone(service))
        return
    service.store.set_state("current_zone", zone)
    text = f"📍 Zone actuelle : <b>{esc_text(zone)}</b>\n\n{service.format_recos(3)}"
    await _reply(update, text, kb_reco(service))


async def cmd_liste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    await _reply(update, *panel_liste(service, 0))


async def _set_done(update: Update, context: ContextTypes.DEFAULT_TYPE, value: bool) -> None:
    service: KairosService = context.application.bot_data["service"]
    ride = service.resolve_ride(" ".join(context.args))
    if ride is None:
        await _reply(update, "Attraction introuvable. Vérifie avec /liste.")
        return
    if value:
        service.store.mark_done(ride.id)
        await _reply(update, f"✅ <b>{esc_text(ride.name)}</b> marquée comme faite.")
    else:
        service.store.unmark_done(ride.id)
        await _reply(update, f"↩️ <b>{esc_text(ride.name)}</b> retirée des attractions faites.")


async def cmd_fait(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_done(update, context, True)


async def cmd_annule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_done(update, context, False)


async def _set_priority(update: Update, context: ContextTypes.DEFAULT_TYPE, value: int) -> None:
    service: KairosService = context.application.bot_data["service"]
    ride = service.resolve_ride(" ".join(context.args))
    if ride is None:
        await _reply(update, "Attraction introuvable. Vérifie avec /liste.")
        return
    value = max(1, min(3, value))
    service.store.set_priority(ride.id, value)
    await _reply(update, f"🎯 <b>{esc_text(ride.name)}</b> → priorité {value}.")


async def cmd_ajouter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_priority(update, context, 3)


async def cmd_retirer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_priority(update, context, 1)


async def cmd_prio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    args = context.args or []
    if len(args) < 2 or args[-1] not in {"1", "2", "3"}:
        await _reply(update, "Usage : <code>/prio Dragon Khan 3</code> (1 = si le temps, 3 = incontournable).")
        return
    ride = service.resolve_ride(" ".join(args[:-1]))
    if ride is None:
        await _reply(update, "Attraction introuvable. Vérifie avec /liste.")
        return
    level = int(args[-1])
    service.store.set_priority(ride.id, level)
    await _reply(update, f"🎯 <b>{esc_text(ride.name)}</b> → priorité {level}.")


async def cmd_seuil(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    args = context.args or []
    if not args:
        await _reply(update, *panel_reglages(service))
        return
    if len(args) == 1 and args[0].isdigit():
        service.alerter.set_default_threshold(int(args[0]))
        await _reply(update, f"🔔 Seuil par défaut réglé à <b>{int(args[0])} min</b>.")
        return
    if args[-1].isdigit():
        ride = service.resolve_ride(" ".join(args[:-1]))
        if ride is None:
            await _reply(update, "Attraction introuvable. Vérifie avec /liste.")
            return
        value = int(args[-1])
        service.alerter.set_ride_threshold(ride.id, value if value > 0 else None)
        if value > 0:
            await _reply(update, f"🔔 Seuil de <b>{esc_text(ride.name)}</b> réglé à {value} min.")
        else:
            await _reply(update, f"🔔 Seuil spécifique retiré pour <b>{esc_text(ride.name)}</b>.")
        return
    await _reply(update, "Usage : <code>/seuil 20</code> ou <code>/seuil Dragon Khan 30</code>.")


async def cmd_alertes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Portée des notifications : toutes les attractions ou seulement les coasters."""
    service: KairosService = context.application.bot_data["service"]
    args = [a.lower() for a in (context.args or [])]
    if args and args[0] in ("coasters", "coaster"):
        service.alerter.set_alert_scope("coasters")
        await _reply(update, "🎢 Alertes limitées aux <b>montagnes russes</b>.")
        return
    if args and args[0] in ("toutes", "tous", "all"):
        service.alerter.set_alert_scope("all")
        await _reply(update, "🔔 Alertes pour <b>toutes les attractions</b>.")
        return
    scope = service.alerter.alert_scope()
    label = "🎢 montagnes russes uniquement" if scope == "coasters" else "🔔 toutes les attractions"
    await _reply(
        update,
        f"Portée des alertes : <b>{label}</b>.\n"
        "Usage : <code>/alertes coasters</code> ou <code>/alertes toutes</code>.",
    )


async def cmd_parc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    statuses = service.engine.statuses(service.current_zone())
    lines = ["🏟️ <b>Occupation par parc</b>"]
    for park in service.parks:
        park_statuses = [s for s in statuses if s.ride.park == park["key"] and s.is_open]
        if not park_statuses:
            lines.append(f"\n<b>{esc_text(park['name'])}</b> — fermé")
            continue
        waits = [s.wait_minutes or 0 for s in park_statuses]
        avg = sum(waits) / len(waits)
        busiest = max(park_statuses, key=lambda s: s.wait_minutes or 0)
        lines.append(
            f"\n<b>{esc_text(park['name'])}</b>\n"
            f"Attente moyenne : <b>{avg:.0f} min</b> ({len(park_statuses)} attractions)\n"
            f"Plus chargée : {esc_text(busiest.ride.name)} ({busiest.wait_minutes} min)"
        )
    await _reply(update, "\n".join(lines), InlineKeyboardMarkup([[_back_menu()]]))


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    args = context.args or []
    if not args or args[0] not in {"collect", "trip"}:
        await _reply(update, *panel_reglages(service))
        return
    service.store.set_state("mode", args[0])
    if args[0] == "trip":
        await _reply(update, "🟢 Mode <b>séjour</b> activé : alertes et rappels enclenchés.")
    else:
        await _reply(update, "🟡 Mode <b>collecte</b> : plus d'alertes, on accumule l'historique.")


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    await service.poll_once()
    await _reply(update, *panel_reco(service))


async def _toggle_hidden(update: Update, context: ContextTypes.DEFAULT_TYPE, hidden: bool) -> None:
    service: KairosService = context.application.bot_data["service"]
    ride = service.resolve_ride(" ".join(context.args))
    if ride is None:
        await _reply(update, "Attraction introuvable. Vérifie avec /liste.")
        return
    service.store.set_ride_hidden(ride.id, hidden)
    if hidden:
        await _reply(update, f"🙈 <b>{esc_text(ride.name)}</b> masquée — elle reste collectée, "
                             "mais n'apparaît plus dans /liste, /ou ni le dashboard.")
    else:
        await _reply(update, f"👁️ <b>{esc_text(ride.name)}</b> réaffichée.")


async def cmd_masquer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _toggle_hidden(update, context, True)


async def cmd_afficher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _toggle_hidden(update, context, False)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: KairosService = context.application.bot_data["service"]
    stats = service.store.stats()
    done = service.store.done_ids()
    first = stats["first"].strftime("%d/%m/%Y") if stats["first"] else "—"
    last = stats["last"].strftime("%d/%m %H:%M") if stats["last"] else "—"
    lines = [
        "📊 <b>État du service</b>",
        f"Mode : <b>{service.mode}</b>",
        f"Zone actuelle : {esc_text(service.current_zone() or '—')}",
        f"Snapshots : <b>{stats['snapshots']}</b> (du {first} au {last})",
        f"Attractions faites : <b>{len(done)}</b> / {len(service.rides())}",
        f"Dernier poll : {service.last_poll.strftime('%d/%m %H:%M:%S') if service.last_poll else '—'}",
    ]
    if service.last_error:
        lines.append(f"⚠️ Dernière erreur : <code>{esc_text(service.last_error)}</code>")
    warning = service.hours_warning()
    if warning:
        lines.append(f"🗓️ {esc_text(warning)}")
    await _reply(update, "\n".join(lines), InlineKeyboardMarkup([[_back_menu()]]))


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat:
        await _reply(update, f"🆔 <code>{chat.id}</code> ({esc_text(chat.type)})")


def _test_sample(target) -> str:
    return (
        "🔔 <b>Test de notification</b>\n\n"
        "Voici le rendu d'une alerte en mode séjour :\n\n"
        "⚡ <b>Shambhala</b> — 12 min (seuil 15) · Chine\n"
        "<i>C'est le moment, fonce !</i>\n\n"
        f"<i>Chat ciblé : <code>{target}</code></i>"
    )


async def _send_test(service: KairosService, fallback_chat_id: int | None = None) -> bool:
    """Envoie une notification de test via le notifier. Renvoie False si aucun canal."""
    target = (
        service.settings.chat_id
        or service.store.get_state("telegram_chat_id")
        or fallback_chat_id
    )
    if service.notifier is None or not target:
        return False
    await service.notifier(_test_sample(target))
    return True


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envoie une notification de test en empruntant le même chemin que les alertes."""
    service: KairosService = context.application.bot_data["service"]
    chat = update.effective_chat
    if chat is None:
        return

    target = service.settings.chat_id or service.store.get_state("telegram_chat_id") or chat.id
    sample = _test_sample(target)

    if service.notifier is not None:
        await service.notifier(sample)
    else:
        await _reply(update, sample)

    notes = [f"✅ Test envoyé vers <code>{target}</code>. Si tu ne le vois pas, vérifie l'ID du groupe."]
    if service.mode != "trip":
        notes.append("ℹ️ Mode <b>collect</b> actif : les alertes automatiques sont désactivées. Bascule avec /mode trip.")
    await _reply(update, "\n".join(notes))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    service: KairosService = context.application.bot_data["service"]
    data = query.data or ""

    if data == "noop":
        await query.answer()
        return

    if data == "test:notify":
        await query.answer("Notification de test envoyée ✅", show_alert=True)
        await _send_test(service)
        return

    # Bouton « Actualiser » : force un poll puis remet à jour le panneau « Où aller ».
    if data == "menu:refresh":
        await query.answer()
        await _safe_edit(query, "⏳ Actualisation auprès du parc…", None)
        await service.poll_once()
        text, markup = panel_reco(service)
        await _safe_edit(query, text, markup)
        return

    await query.answer()

    if data == "menu:main":
        text, markup = panel_menu(service)
    elif data == "menu:reco":
        text, markup = panel_reco(service)
    elif data == "menu:etat" or data.startswith("menu:etat:"):
        park = data.split(":", 2)[2] if data.count(":") == 2 else None
        text, markup = panel_etat(service, park)
    elif data == "menu:zone":
        text, markup = panel_zone(service)
    elif data == "menu:liste" or data.startswith("menu:liste:"):
        page = int(data.split(":")[2]) if data.count(":") == 2 else 0
        text, markup = panel_liste(service, page)
    elif data == "menu:reglages":
        text, markup = panel_reglages(service)
    elif data == "alertes:toggle":
        service.alerter.set_alert_scope(
            "all" if service.alerter.alert_scope() == "coasters" else "coasters"
        )
        text, markup = panel_reglages(service)
    elif data == "menu:aide":
        text, markup = HELP_TEXT, InlineKeyboardMarkup([[_back_menu()]])
    elif data.startswith("zone:set:"):
        index = int(data.split(":")[2])
        zones = service.zones()
        if 0 <= index < len(zones):
            service.store.set_state("current_zone", zones[index])
        text, markup = panel_zone(service)
    elif data.startswith("toggle:"):
        _, ride_id, page = data.split(":")
        ride_id, page = int(ride_id), int(page)
        if ride_id in service.store.done_ids():
            service.store.unmark_done(ride_id)
        else:
            service.store.mark_done(ride_id)
        text, markup = panel_liste(service, page)
    elif data.startswith("done:"):
        ride_id = int(data.split(":")[1])
        service.store.mark_done(ride_id)
        text, markup = panel_reco(service)
    elif data.startswith("seuil:"):
        delta = int(data.split(":")[1])
        new_value = max(1, service.alerter.default_threshold() + delta)
        service.alerter.set_default_threshold(new_value)
        text, markup = panel_reglages(service)
    elif data == "mode:toggle":
        service.store.set_state("mode", "collect" if service.mode == "trip" else "trip")
        text, markup = panel_reglages(service)
    else:
        text, markup = panel_menu(service)

    await _safe_edit(query, text, markup)


def resolve_allowed_chats(service: KairosService, settings: Settings) -> set[int] | None:
    """Chats autorisés à piloter le bot.

    ``KAIROS_TELEGRAM_CHAT_ID`` est la source de vérité. À défaut, on retombe sur l'id
    mémorisé lors d'un premier ``/id``. Si rien n'est connu, on renvoie ``None`` =
    mode « amorçage » (tout est accepté) pour permettre de récupérer l'id ; un
    avertissement est alors journalisé au démarrage.
    """
    if settings.chat_id is not None:
        return {settings.chat_id}
    stored = service.store.get_state("telegram_chat_id")
    if stored is not None:
        try:
            return {int(stored)}
        except (TypeError, ValueError):
            pass
    return None


async def guard_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rejette tout update venant d'un chat non autorisé (coupe les handlers suivants)."""
    allowed: set[int] | None = context.application.bot_data.get("allowed_chats")
    if allowed is None:  # mode amorçage : aucune restriction configurée
        return
    chat = update.effective_chat
    if chat is not None and chat.id in allowed:
        return
    user = update.effective_user
    logger.warning(
        "Update ignoré : chat non autorisé %s (utilisateur %s)",
        chat.id if chat else None,
        user.id if user else None,
    )
    raise ApplicationHandlerStop


async def track_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enregistre l'id du chat pour permettre les alertes proactives.

    Ne mémorise jamais un chat inconnu dès qu'une restriction est active, sans quoi
    n'importe qui pourrait détourner les alertes vers son chat.
    """
    chat = update.effective_chat
    if chat is None:
        return
    allowed: set[int] | None = context.application.bot_data.get("allowed_chats")
    if allowed is not None and chat.id not in allowed:
        return
    service: KairosService | None = context.application.bot_data.get("service")
    if service is not None:
        service.store.set_state("telegram_chat_id", chat.id)


# ----------------------------------------------------------------- builder --- #


def _telegram_requests(settings: Settings) -> tuple[HTTPXRequest, HTTPXRequest]:
    """Construit les clients HTTP de Telegram (IPv4 forcé, timeouts généreux)."""
    common: dict = {}
    if settings.force_ipv4:
        common["httpx_kwargs"] = {"transport": ipv4_transport()}
    request = HTTPXRequest(
        connect_timeout=20.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=20.0, **common
    )
    # getUpdates est une requête longue : on lui donne plus de temps de lecture.
    get_updates_request = HTTPXRequest(
        connect_timeout=20.0, read_timeout=40.0, write_timeout=30.0, pool_timeout=20.0, **common
    )
    return request, get_updates_request


def log_polling_error(error: TelegramError) -> None:
    """Journalise une erreur de long polling sans noyer les logs.

    Les erreurs réseau transitoires (typiquement au réveil après une mise en veille,
    quand la connexion TCP est périmée) sont réessayées indéfiniment par PTB : on se
    contente d'un avertissement lisible. Les autres erreurs Telegram gardent leur trace.
    """
    # Attention : dans PTB, BadRequest hérite de NetworkError alors que c'est une erreur
    # métier (requête invalide) — on l'exclut pour ne pas masquer un vrai problème.
    if isinstance(error, NetworkError) and not isinstance(error, BadRequest):
        logger.warning("Polling Telegram interrompu (réseau) : %s — reconnexion…", error)
    else:
        logger.error("Erreur de polling Telegram : %s", error, exc_info=error)


def build_application(service: KairosService, settings: Settings) -> Application:
    request, get_updates_request = _telegram_requests(settings)
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )
    allowed = resolve_allowed_chats(service, settings)
    if allowed is None:
        logger.warning(
            "Aucun chat autorisé (KAIROS_TELEGRAM_CHAT_ID vide) : le bot répond à tout le monde. "
            "Envoie /id dans ton groupe puis renseigne KAIROS_TELEGRAM_CHAT_ID pour le verrouiller."
        )
    else:
        logger.info("Commandes Telegram restreintes au(x) chat(s) : %s",
                    ", ".join(str(cid) for cid in sorted(allowed)))

    app.bot_data["service"] = service
    app.bot_data["allowed_chats"] = allowed

    # groupe -2 : rejet des chats inconnus avant tout autre handler
    app.add_handler(TypeHandler(Update, guard_chat), group=-2)
    app.add_handler(TypeHandler(Update, track_chat), group=-1)
    app.add_handler(CommandHandler(["start"], cmd_start))
    app.add_handler(CommandHandler(["aide", "help"], cmd_aide))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("ou", cmd_ou))
    app.add_handler(CommandHandler("etat", cmd_etat))
    app.add_handler(CommandHandler("zone", cmd_zone))
    app.add_handler(CommandHandler("liste", cmd_liste))
    app.add_handler(CommandHandler("fait", cmd_fait))
    app.add_handler(CommandHandler("annule", cmd_annule))
    app.add_handler(CommandHandler("ajouter", cmd_ajouter))
    app.add_handler(CommandHandler("retirer", cmd_retirer))
    app.add_handler(CommandHandler("prio", cmd_prio))
    app.add_handler(CommandHandler("seuil", cmd_seuil))
    app.add_handler(CommandHandler("alertes", cmd_alertes))
    app.add_handler(CommandHandler("parc", cmd_parc))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("masquer", cmd_masquer))
    app.add_handler(CommandHandler("afficher", cmd_afficher))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app


def make_notifier(app: Application, service: KairosService, settings: Settings):
    """Fabrique la fonction d'envoi utilisée par le service pour les alertes."""

    async def notify(text: str) -> None:
        chat_id = settings.chat_id or service.store.get_state("telegram_chat_id")
        if not chat_id:
            logger.warning("Aucun chat_id connu : alerte ignorée (%s)", text[:60])
            return
        for chunk in split_message(text):
            await app.bot.send_message(chat_id=int(chat_id), text=chunk, parse_mode=ParseMode.HTML)

    return notify
