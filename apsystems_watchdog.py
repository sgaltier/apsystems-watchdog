#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apsystems_watchdog.py
=====================

Surveillance d'une installation photovoltaïque APsystems via l'OpenAPI officiel
(https://api.apsystemsema.com:9282) avec envoi de notifications.

Détecte :
  1. Le voyant système "light" != 1 (2 = alarme onduleur, 3 = ECU hors ligne,
     4 = aucune donnée remontée)  -> couvre le cas "ECU débranché / plus de réseau"
  2. Une puissance instantanée nulle alors que le soleil est suffisamment haut
     -> couvre EXACTEMENT le cas "disjoncteur qui a sauté pendant l'orage"
  3. L'API elle-même injoignable N fois de suite

Envoie une alerte (ntfy / Telegram / e-mail SMTP) une seule fois par incident,
puis un message de retour à la normale.

Ping optionnel de Healthchecks.io à chaque exécution réussie : c'est le
"dead man's switch" qui vous prévient si le script lui-même (ou la machine, ou
la box Internet) s'arrête. Sans ça, un script mort = un silence indétectable.

Dépendances : aucune (bibliothèque standard Python 3.10+).

-------------------------------------------------------------------------------
CONFIGURATION (variables d'environnement)
-------------------------------------------------------------------------------
Obligatoires :
  APS_APP_ID          App Id  (32 caractères) fourni par APsystems
  APS_APP_SECRET      App Secret (12 caractères) fourni par APsystems
  APS_SID             Identifiant de votre système (visible dans EMA)

Localisation (pour calculer la hauteur du soleil) :
  PV_LAT              Latitude  (défaut 45.75  = Lyon)
  PV_LON              Longitude (défaut  4.85  = Lyon)

Notifications (au moins une) :
  NTFY_TOPIC          Sujet ntfy.sh, ex. "pv-alerte-xyz123" (le plus simple)
  NTFY_SERVER         Défaut https://ntfy.sh
  TELEGRAM_TOKEN      Token du bot Telegram
  TELEGRAM_CHAT_ID    Identifiant du chat
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM / SMTP_TO

Réglages (facultatifs) :
  PV_MIN_ELEVATION    Hauteur du soleil (deg) au-delà de laquelle on attend de
                      la production. Défaut 15.
  PV_MIN_POWER_W      Seuil de puissance considéré comme "nul". Défaut 20 W.
  PV_GRACE_MINUTES    Durée de production nulle avant alerte. Défaut 60 min.
  PV_STATE_FILE       Fichier d'état. Défaut ~/.apsystems_watchdog.json
  HEALTHCHECKS_URL    URL de ping Healthchecks.io (dead man's switch)

Usage :
  python3 apsystems_watchdog.py              # une passe (à mettre en cron)
  python3 apsystems_watchdog.py --test        # teste juste les notifications
  python3 apsystems_watchdog.py --dry-run     # affiche l'état, n'alerte pas

Cron conseillé (toutes les 20 min, de 7h à 21h -- l'API est facturée à l'usage,
inutile de la solliciter la nuit) :
  */20 7-21 * * *  /usr/bin/python3 /opt/pv/apsystems_watchdog.py >> /var/log/pv.log 2>&1
"""

import base64
import hashlib
import hmac
import json
import math
import os
import ssl
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE_URL = "https://api.apsystemsema.com:9282"

APP_ID = os.getenv("APS_APP_ID", "")
APP_SECRET = os.getenv("APS_APP_SECRET", "")
SID = os.getenv("APS_SID", "")

LAT = float(os.getenv("PV_LAT", "45.75"))
LON = float(os.getenv("PV_LON", "4.85"))

MIN_ELEVATION = float(os.getenv("PV_MIN_ELEVATION", "15"))
MIN_POWER_W = float(os.getenv("PV_MIN_POWER_W", "20"))
GRACE_MINUTES = int(os.getenv("PV_GRACE_MINUTES", "60"))
MAX_API_FAILURES = int(os.getenv("PV_MAX_API_FAILURES", "3"))

STATE_FILE = Path(os.getenv("PV_STATE_FILE", "~/.apsystems_watchdog.json")).expanduser()
HEALTHCHECKS_URL = os.getenv("HEALTHCHECKS_URL", "")

# Le manuel définit RequestPath comme "the last segment of the path".
# Certaines implémentations signent le chemin complet. Si vous obtenez
# systématiquement un code 3002 (signature invalide), basculez ce drapeau.
SIGN_FULL_PATH = os.getenv("APS_SIGN_FULL_PATH", "0") == "1"

TIMEOUT = 20


# --------------------------------------------------------------------------- #
# Signature et appels API
# --------------------------------------------------------------------------- #

def _sign(string_to_sign: str) -> str:
    """HmacSHA256(stringToSign, appSecret) encodé en Base64, cf. §2.2.2."""
    digest = hmac.new(
        APP_SECRET.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def api_get(path: str, params: dict | None = None) -> dict:
    """Appel GET signé de l'OpenAPI. Lève RuntimeError si code != 0."""
    timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    nonce = uuid.uuid4().hex  # chaîne de 32 caractères, cf. §2.2.1
    method = "GET"
    sig_method = "HmacSHA256"

    request_path = path if SIGN_FULL_PATH else path.rstrip("/").split("/")[-1]
    string_to_sign = "/".join(
        [timestamp, nonce, APP_ID, request_path, method, sig_method]
    )

    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, method=method)
    req.add_header("X-CA-AppId", APP_ID)
    req.add_header("X-CA-Timestamp", timestamp)
    req.add_header("X-CA-Nonce", nonce)
    req.add_header("X-CA-Signature-Method", sig_method)
    req.add_header("X-CA-Signature", _sign(string_to_sign))

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    code = int(payload.get("code", -1))
    if code != 0:
        raise RuntimeError(f"OpenAPI code {code} sur {path} ({CODES.get(code, '?')})")
    return payload.get("data")


# Extrait de l'annexe 4.1 du manuel, pour des messages d'erreur lisibles.
CODES = {
    1001: "Aucune donnée",
    2001: "Compte applicatif invalide",
    2002: "Compte non autorisé",
    2003: "Autorisation expirée",
    2005: "Quota d'appels dépassé",
    3002: "Signature invalide",
    3003: "Token expiré",
    4001: "Paramètre invalide",
    5000: "Erreur serveur",
    7001: "Limite d'accès dépassée",
    7002: "Trop de requêtes",
}


# --------------------------------------------------------------------------- #
# Position du soleil (NOAA simplifié) -- évite les fausses alertes la nuit,
# en hiver et par très mauvais temps.
# --------------------------------------------------------------------------- #

def solar_elevation(lat: float, lon: float, when_utc: datetime) -> float:
    n = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    gamma = 2 * math.pi / 365 * (n - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.001480 * math.sin(3 * gamma)
    )
    tst = hour * 60 + eqtime + 4 * lon
    ha = math.radians(tst / 4 - 180)
    latr = math.radians(lat)
    cosz = math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, cosz))))


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #

def _post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=TIMEOUT).read()


def notify(title: str, message: str, urgent: bool = True) -> None:
    """Envoie sur tous les canaux configurés. Un canal en échec n'en bloque pas un autre."""
    sent = False

    topic = os.getenv("NTFY_TOPIC")
    if topic:
        try:
            server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
            _post(
                f"{server}/{topic}",
                message.encode("utf-8"),
                {
                    "Title": title.encode("utf-8").decode("latin-1", "replace"),
                    "Priority": "urgent" if urgent else "default",
                    "Tags": "warning" if urgent else "white_check_mark",
                },
            )
            sent = True
        except Exception as exc:  # noqa: BLE001
            print(f"[ntfy] échec : {exc}", file=sys.stderr)

    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            body = urllib.parse.urlencode(
                {"chat_id": chat, "text": f"{title}\n\n{message}"}
            ).encode()
            _post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                body,
                {"Content-Type": "application/x-www-form-urlencoded"},
            )
            sent = True
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram] échec : {exc}", file=sys.stderr)

    if os.getenv("SMTP_HOST"):
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = title
            msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", ""))
            msg["To"] = os.getenv("SMTP_TO", "")
            msg.set_content(message)
            port = int(os.getenv("SMTP_PORT", "587"))
            with smtplib.SMTP(os.getenv("SMTP_HOST"), port, timeout=TIMEOUT) as smtp:
                smtp.starttls()
                if os.getenv("SMTP_USER"):
                    smtp.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS", ""))
                smtp.send_message(msg)
            sent = True
        except Exception as exc:  # noqa: BLE001
            print(f"[smtp] échec : {exc}", file=sys.stderr)

    if not sent:
        print(f"[!] AUCUN canal de notification n'a fonctionné : {title} — {message}",
              file=sys.stderr)


# --------------------------------------------------------------------------- #
# État persistant (anti-spam + mémoire du "depuis quand c'est à zéro")
# --------------------------------------------------------------------------- #

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# --------------------------------------------------------------------------- #
# Collecte
# --------------------------------------------------------------------------- #

LIGHT_LABELS = {
    1: "Vert — fonctionnement normal",
    2: "Jaune — un ou plusieurs micro-onduleurs en alarme",
    3: "Rouge — problème de connexion réseau de l'ECU",
    4: "Gris — aucune donnée remontée par l'ECU",
}


def latest_power_w(sid: str, eid: str, day: str) -> tuple[float, str]:
    """Dernière puissance connue (W) et heure, via la télémétrie 'minutely' de l'ECU."""
    data = api_get(
        f"/user/api/v2/systems/{sid}/devices/ecu/energy/{eid}",
        {"energy_level": "minutely", "date_range": day},
    )
    if not isinstance(data, dict):
        return 0.0, "?"
    powers = data.get("power") or []
    times = data.get("time") or []
    if not powers:
        return 0.0, "?"
    try:
        return float(powers[-1]), (times[-1] if times else "?")
    except (TypeError, ValueError):
        return 0.0, "?"


def collect() -> dict:
    """Retourne un instantané de l'installation."""
    details = api_get(f"/user/api/v2/systems/details/{SID}")
    summary = api_get(f"/user/api/v2/systems/summary/{SID}")

    ecus = details.get("ecu") or []
    # Le fuseau renvoyé par l'API sert à déterminer "aujourd'hui" côté installation.
    day = datetime.now().strftime("%Y-%m-%d")

    per_ecu = {}
    for eid in ecus:
        try:
            per_ecu[eid] = latest_power_w(SID, eid, day)
        except Exception as exc:  # noqa: BLE001
            per_ecu[eid] = (None, f"erreur: {exc}")

    return {
        "light": int(details.get("light", 0)),
        "capacity": details.get("capacity"),
        "ecus": ecus,
        "today_kwh": summary.get("today"),
        "per_ecu": per_ecu,
    }


# --------------------------------------------------------------------------- #
# Logique d'alerte
# --------------------------------------------------------------------------- #

def run(dry_run: bool = False) -> int:
    state = load_state()
    now = datetime.now(timezone.utc)
    elevation = solar_elevation(LAT, LON, now)

    # --- Appel API, avec tolérance aux pannes passagères -------------------- #
    try:
        snap = collect()
        state["api_failures"] = 0
    except Exception as exc:  # noqa: BLE001
        fails = state.get("api_failures", 0) + 1
        state["api_failures"] = fails
        print(f"[api] échec {fails}/{MAX_API_FAILURES} : {exc}", file=sys.stderr)
        if fails == MAX_API_FAILURES and not dry_run:
            notify(
                "⚠️ Surveillance PV : API APsystems injoignable",
                f"{fails} échecs consécutifs.\nDernière erreur : {exc}\n\n"
                "Cela peut venir du cloud APsystems, de votre connexion "
                "Internet, ou de vos identifiants OpenAPI.",
            )
        save_state(state)
        return 1

    print(f"Soleil : {elevation:.1f}°  |  Voyant : {snap['light']} "
          f"({LIGHT_LABELS.get(snap['light'], '?')})  |  "
          f"Aujourd'hui : {snap['today_kwh']} kWh")
    for eid, (power, ts) in snap["per_ecu"].items():
        print(f"  ECU {eid} : {power} W à {ts}")

    problems: list[str] = []

    # --- 1. Voyant système -------------------------------------------------- #
    if snap["light"] in (3, 4):
        problems.append(f"Voyant système : {LIGHT_LABELS[snap['light']]}.")
    elif snap["light"] == 2:
        problems.append(f"Voyant système : {LIGHT_LABELS[2]}.")

    # --- 2. Production nulle en plein soleil -------------------------------- #
    zero_ecus = [
        eid for eid, (power, _) in snap["per_ecu"].items()
        if power is not None and power < MIN_POWER_W
    ]

    if elevation >= MIN_ELEVATION and zero_ecus:
        first_seen = state.get("zero_since")
        if not first_seen:
            state["zero_since"] = now.isoformat()
            first_seen = state["zero_since"]
        elapsed = now - datetime.fromisoformat(first_seen)
        print(f"  -> production nulle depuis {int(elapsed.total_seconds() // 60)} min")
        if elapsed >= timedelta(minutes=GRACE_MINUTES):
            noms = ", ".join(zero_ecus)
            problems.append(
                f"Production nulle (< {MIN_POWER_W:.0f} W) depuis "
                f"{int(elapsed.total_seconds() // 60)} min sur : {noms}, "
                f"alors que le soleil est à {elevation:.0f}° au-dessus de l'horizon."
            )
    else:
        state.pop("zero_since", None)

    # --- 3. Notification (une seule par incident, + retour à la normale) ---- #
    was_alerting = state.get("alerting", False)

    if problems and not was_alerting:
        message = (
            "\n".join(f"• {p}" for p in problems)
            + f"\n\nProduction du jour : {snap['today_kwh']} kWh"
            + f"\nPuissance instantanée : "
            + ", ".join(f"{eid} = {p} W" for eid, (p, _) in snap["per_ecu"].items())
            + "\n\n👉 Vérifiez les disjoncteurs PV dans le garage, "
              "puis l'alimentation et le réseau de l'ECU."
        )
        if not dry_run:
            notify("🔴 Alerte photovoltaïque", message)
        state["alerting"] = True
        state["alert_since"] = now.isoformat()
        print("ALERTE ENVOYÉE\n" + message)

    elif not problems and was_alerting:
        since = state.get("alert_since", "?")
        if not dry_run:
            notify(
                "✅ Photovoltaïque : retour à la normale",
                f"La production a repris.\nPuissance : "
                + ", ".join(f"{eid} = {p} W" for eid, (p, _) in snap["per_ecu"].items())
                + f"\nIncident ouvert depuis : {since}",
                urgent=False,
            )
        state["alerting"] = False
        state.pop("alert_since", None)
        print("RETOUR À LA NORMALE")

    elif problems:
        print("Problème toujours présent — pas de nouvelle notification (anti-spam).")
    else:
        print("Tout va bien.")

    state["last_check"] = now.isoformat()
    save_state(state)

    # --- 4. Dead man's switch ---------------------------------------------- #
    # Si CE script cesse de tourner (machine éteinte, box HS, cron cassé),
    # Healthchecks.io vous alertera de son côté. C'est la sécurité qui manque
    # à toute surveillance reposant uniquement sur le cloud du fabricant.
    if HEALTHCHECKS_URL and not dry_run:
        try:
            urllib.request.urlopen(HEALTHCHECKS_URL, timeout=10).read()
        except Exception as exc:  # noqa: BLE001
            print(f"[healthchecks] ping échoué : {exc}", file=sys.stderr)

    return 0


def main() -> int:
    if "--test" in sys.argv:
        notify(
            "🔔 Test — surveillance photovoltaïque",
            "Si vous lisez ceci, vos notifications fonctionnent.",
            urgent=False,
        )
        return 0

    missing = [k for k, v in
               (("APS_APP_ID", APP_ID), ("APS_APP_SECRET", APP_SECRET), ("APS_SID", SID))
               if not v]
    if missing:
        print("Variables manquantes : " + ", ".join(missing), file=sys.stderr)
        return 2

    return run(dry_run="--dry-run" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
