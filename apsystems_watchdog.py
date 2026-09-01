#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Copyright (C) 2026 Sébastien Galtier
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
apsystems_watchdog.py
=====================

Surveillance d'une installation photovoltaïque APsystems via l'OpenAPI officiel
(https://api.apsystemsema.com:9282) avec envoi de notifications.

Détecte :
  1. Le voyant système "light" != 1 (2 = alarme onduleur, 3 = ECU hors ligne,
     4 = aucune donnée remontée). Attention : ce voyant est mis à jour très
     paresseusement par le cloud APsystems, il ne suffit pas à lui seul.
  2. Une puissance instantanée nulle alors que le soleil est suffisamment haut.
  3. Des données figées : l'API continue de renvoyer le DERNIER point connu même
     quand l'ECU ne remonte plus rien. C'est ce cas -- et non une puissance
     nulle -- qui se produit réellement quand le disjoncteur saute ou que
     l'installation est débranchée. Sans ce contrôle de fraîcheur, la panne est
     totalement invisible. Même chose pour un ECU muet depuis minuit : l'API
     répond alors "aucune donnée" (code 1001), ce qui est normal avant le lever
     du soleil mais anormal une fois celui-ci haut.
  4. L'API elle-même injoignable N fois de suite

Envoie une alerte (ntfy / Telegram / e-mail SMTP) une seule fois par incident,
puis un message de retour à la normale.

Ping optionnel de Healthchecks.io à chaque exécution réussie : c'est le
"dead man's switch" qui vous prévient si le script lui-même (ou la machine, ou
la box Internet) s'arrête. Sans ça, un script mort = un silence indétectable.

Dépendances : aucune (bibliothèque standard Python 3.10+).

-------------------------------------------------------------------------------
CONFIGURATION (variables d'environnement)
-------------------------------------------------------------------------------
Toutes les variables ci-dessous peuvent être placées dans un fichier
« config.env » situé à côté de ce script (facultatif), au format CLE=valeur,
une par ligne ; les lignes vides et celles commençant par « # » sont ignorées.
L'environnement réel l'emporte sur le fichier. Chemin surchargeable par
PV_CONFIG_FILE. Ce fichier contient des secrets : ne le committez pas.

  APS_APP_SECRET=xxxxxxxxxxxx
  NTFY_TOPIC=pv-alerte-xyz123

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
  PV_MAX_DATA_AGE_MINUTES
                      Âge maximal du dernier point de télémétrie avant de
                      considérer que l'ECU ne remonte plus rien. Défaut 20 min
                      (l'ECU publie toutes les ~5 min).
  PV_STATE_FILE       Fichier d'état. Défaut ~/.apsystems_watchdog.json
  PV_LOG_FILE         Fichier de log. Défaut ~/.apsystems_watchdog.log
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

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = Path(
    os.getenv("PV_CONFIG_FILE", str(SCRIPT_DIR / "config.env"))
).expanduser()


def load_env_file(path: Path) -> None:
    """Charge un fichier CLE=valeur dans os.environ. Absent = sans effet.

    L'environnement réel l'emporte : le fichier ne fournit que des valeurs par
    défaut, ce qui permet de surcharger ponctuellement une variable depuis le
    shell ou la crontab sans éditer le fichier.

    Un « # » n'est reconnu comme commentaire qu'en début de ligne : tronquer une
    valeur au premier « # » mutilerait silencieusement un mot de passe ou un
    token qui en contient un.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError as exc:
        # log() n'existe pas encore à ce stade : LOG_FILE en dépend.
        print(f"[config] lecture impossible de {path} : {exc}", file=sys.stderr)
        return

    for lineno, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            print(f"[config] {path}:{lineno} ignorée (format attendu CLE=valeur)",
                  file=sys.stderr)
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(CONFIG_FILE)

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
MAX_DATA_AGE_MINUTES = int(os.getenv("PV_MAX_DATA_AGE_MINUTES", "20"))

STATE_FILE = Path(os.getenv("PV_STATE_FILE", "./apsystems_watchdog.json")).expanduser()
LOG_FILE = Path(os.getenv("PV_LOG_FILE", "./apsystems_watchdog.log")).expanduser()
HEALTHCHECKS_URL = os.getenv("HEALTHCHECKS_URL", "")

# Notifications (au moins un canal)
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = os.getenv("SMTP_PORT", "587")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "")
SMTP_TO = os.getenv("SMTP_TO", "")

# Le manuel définit RequestPath comme "the last segment of the path".
# Certaines implémentations signent le chemin complet. Si vous obtenez
# systématiquement un code 3002 (signature invalide), basculez ce drapeau.
SIGN_FULL_PATH = os.getenv("APS_SIGN_FULL_PATH", "0") == "1"

TIMEOUT = 20


# --------------------------------------------------------------------------- #
# Journalisation
# --------------------------------------------------------------------------- #

def log(message: str, *, err: bool = False) -> None:
    """Affiche et journalise `message` en une seule ligne, précédée de la date/heure."""
    one_liner = " ".join(str(message).split("\n"))
    line = f"{datetime.now().isoformat(timespec='seconds')} {one_liner}"
    # flush=True : un stdout redirigé vers un pipe (cas du conteneur) est
    # bufferisé par blocs. Sans vidage explicite, le journal du conteneur reste
    # vide tant que le buffer n'est pas plein ; PYTHONUNBUFFERED ne peut pas
    # être supposé présent selon la façon dont l'image est lancée.
    print(line, file=sys.stderr if err else sys.stdout, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        print(f"[log] échec d'écriture dans {LOG_FILE} : {exc}",
              file=sys.stderr, flush=True)


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
        raise ApiError(code, path)
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

NO_DATA_CODE = 1001


class ApiError(RuntimeError):
    """Erreur applicative de l'OpenAPI (champ « code » non nul)."""

    def __init__(self, code: int, path: str):
        super().__init__(f"OpenAPI code {code} sur {path} ({CODES.get(code, '?')})")
        self.code = code


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


def notify(title: str, message: str, urgent: bool = True) -> bool:
    """Envoie sur tous les canaux configurés. Un canal en échec n'en bloque pas un autre.

    Retourne True si au moins un canal a effectivement reçu le message.
    """
    sent = False
    configured = bool(NTFY_TOPIC or (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID) or SMTP_HOST)

    if NTFY_TOPIC:
        try:
            server = NTFY_SERVER.rstrip("/")
            _post(
                f"{server}/{NTFY_TOPIC}",
                message.encode("utf-8"),
                {
                    "Title": title.encode("utf-8").decode("latin-1", "replace"),
                    "Priority": "urgent" if urgent else "default",
                    "Tags": "warning" if urgent else "white_check_mark",
                },
            )
            sent = True
        except Exception as exc:  # noqa: BLE001
            log(f"[ntfy] échec : {exc}", err=True)

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            body = urllib.parse.urlencode(
                {"chat_id": TELEGRAM_CHAT_ID, "text": f"{title}\n\n{message}"}
            ).encode()
            _post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                body,
                {"Content-Type": "application/x-www-form-urlencoded"},
            )
            sent = True
        except Exception as exc:  # noqa: BLE001
            log(f"[telegram] échec : {exc}", err=True)

    if SMTP_HOST:
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = title
            msg["From"] = SMTP_FROM
            msg["To"] = SMTP_TO
            msg.set_content(message)
            with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=TIMEOUT) as smtp:
                smtp.starttls()
                if SMTP_USER:
                    smtp.login(SMTP_USER, SMTP_PASS or "")
                smtp.send_message(msg)
            sent = True
        except Exception as exc:  # noqa: BLE001
            log(f"[smtp] échec : {exc}", err=True)

    if not sent:
        cause = ("AUCUN canal n'est configuré (NTFY_TOPIC / TELEGRAM_* / SMTP_*)"
                 if not configured else "tous les canaux configurés ont échoué")
        log(f"[!] Notification NON DÉLIVRÉE — {cause} : {title} — {message}", err=True)
    return sent


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


def latest_power_w(sid: str, eid: str, day: str) -> tuple[float | None, str | None]:
    """Dernière puissance connue (W) et heure, via la télémétrie 'minutely' de l'ECU.

    Retourne (None, None) tant que l'API n'a aucun point pour `day` : c'est le
    cas normal chaque matin avant le réveil de l'ECU (elle répond alors code
    1001), mais aussi le cas d'un ECU resté muet depuis minuit -- il ne faut
    donc surtout pas le confondre avec une puissance nulle.
    """
    try:
        data = api_get(
            f"/user/api/v2/systems/{sid}/devices/ecu/energy/{eid}",
            {"energy_level": "minutely", "date_range": day},
        )
    except ApiError as exc:
        if exc.code == NO_DATA_CODE:
            return None, None
        raise
    if not isinstance(data, dict):
        return None, None
    powers = data.get("power") or []
    times = data.get("time") or []
    if not powers:
        return None, None
    try:
        return float(powers[-1]), (times[-1] if times else "?")
    except (TypeError, ValueError):
        return None, None


def data_age_minutes(day: str, ts: str) -> float | None:
    """Âge en minutes du point de télémétrie horodaté `ts`, ou None si illisible.

    L'API ne dit jamais "je n'ai plus de données" : elle rejoue indéfiniment le
    dernier point reçu. Seul son horodatage trahit un ECU muet.
    """
    stamp = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            stamp = datetime.strptime(f"{day} {ts}", fmt)
            break
        except ValueError:
            continue
    if stamp is None:
        try:
            stamp = datetime.fromisoformat(ts)
        except ValueError:
            return None
    return (datetime.now() - stamp).total_seconds() / 60


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
            power, ts = latest_power_w(SID, eid, day)
            age = data_age_minutes(day, ts) if ts else None
            per_ecu[eid] = (power, ts, age)
        except Exception as exc:  # noqa: BLE001
            per_ecu[eid] = (None, f"erreur: {exc}", None)

    return {
        "light": int(details.get("light", 0)),
        "capacity": details.get("capacity"),
        "ecus": ecus,
        "today_kwh": summary.get("today"),
        "per_ecu": per_ecu,
    }


# --------------------------------------------------------------------------- #
# Mise en forme
# --------------------------------------------------------------------------- #

NO_DATA_TEXT = "aucune donnée depuis minuit"


def describe_ecu(eid: str, power, ts, age) -> str:
    """Ligne lisible pour un ECU : mesure, absence de données ou erreur."""
    if ts is None:
        return f"{eid} = {NO_DATA_TEXT}"
    if power is None:
        return f"{eid} = {ts}"
    return (f"{eid} = {power} W à {ts}"
            + (f" (il y a {age:.0f} min)" if age is not None else ""))


def describe_power(per_ecu: dict) -> str:
    return ", ".join(
        f"{eid} = " + (f"{power} W" if power is not None else NO_DATA_TEXT)
        for eid, (power, _, _) in per_ecu.items()
    )


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
        log(f"[api] échec {fails}/{MAX_API_FAILURES} : {exc}", err=True)
        if fails == MAX_API_FAILURES and not dry_run:
            notify(
                "⚠️ Surveillance PV : API APsystems injoignable",
                f"{fails} échecs consécutifs.\nDernière erreur : {exc}\n\n"
                "Cela peut venir du cloud APsystems, de votre connexion "
                "Internet, ou de vos identifiants OpenAPI.",
            )
        save_state(state)
        return 1

    ecu_details = ", ".join(
        describe_ecu(eid, power, ts, age)
        for eid, (power, ts, age) in snap["per_ecu"].items()
    )
    today_kwh = snap["today_kwh"]
    today_text = f"{today_kwh} kWh" if today_kwh is not None else "pas encore de relevé"
    summary = (
        f"Soleil : {elevation:.1f}°  |  Voyant : {snap['light']} "
        f"({LIGHT_LABELS.get(snap['light'], '?')})  |  "
        f"Aujourd'hui : {today_text}  |  ECUs : {ecu_details}"
    )

    problems: list[str] = []

    # --- 1. Voyant système -------------------------------------------------- #
    if snap["light"] in (3, 4):
        problems.append(f"Voyant système : {LIGHT_LABELS[snap['light']]}.")
    elif snap["light"] == 2:
        problems.append(f"Voyant système : {LIGHT_LABELS[2]}.")

    # --- 2. Production nulle en plein soleil -------------------------------- #
    zero_ecus = [
        eid for eid, (power, _, _) in snap["per_ecu"].items()
        if power is not None and power < MIN_POWER_W
    ]

    if elevation >= MIN_ELEVATION and zero_ecus:
        first_seen = state.get("zero_since")
        if not first_seen:
            state["zero_since"] = now.isoformat()
            first_seen = state["zero_since"]
        elapsed = now - datetime.fromisoformat(first_seen)
        log(f"  -> production nulle depuis {int(elapsed.total_seconds() // 60)} min")
        if elapsed >= timedelta(minutes=GRACE_MINUTES):
            noms = ", ".join(zero_ecus)
            problems.append(
                f"Production nulle (< {MIN_POWER_W:.0f} W) depuis "
                f"{int(elapsed.total_seconds() // 60)} min sur : {noms}, "
                f"alors que le soleil est à {elevation:.0f}° au-dessus de l'horizon."
            )
    else:
        state.pop("zero_since", None)

    # --- 3. Données figées : l'ECU ne remonte plus rien --------------------- #
    # C'est LE symptôme réel d'un débranchement ou d'un disjoncteur qui a sauté :
    # la puissance ne tombe pas à zéro, elle cesse simplement d'être mise à jour.
    stale_ecus = [
        (eid, age) for eid, (_, _, age) in snap["per_ecu"].items()
        if age is not None and age > MAX_DATA_AGE_MINUTES
    ]
    # Un ECU muet depuis minuit ne produit aucun point du tout : l'API répond
    # "aucune donnée" et non un point périmé. Sans ce cas, une panne survenue de
    # nuit resterait invisible toute la journée suivante.
    silent_ecus = [eid for eid, (_, ts, _) in snap["per_ecu"].items() if ts is None]

    if elevation >= MIN_ELEVATION and (stale_ecus or silent_ecus):
        noms = ", ".join(
            [f"{eid} (dernier point il y a {age:.0f} min)" for eid, age in stale_ecus]
            + [f"{eid} (aucun point depuis minuit)" for eid in silent_ecus]
        )
        problems.append(
            f"Aucune donnée fraîche depuis plus de {MAX_DATA_AGE_MINUTES} min "
            f"sur : {noms}. L'ECU ne remonte plus rien alors que le soleil est "
            f"à {elevation:.0f}° au-dessus de l'horizon."
        )

    # --- 4. Notification (une seule par incident, + retour à la normale) ---- #
    was_alerting = state.get("alerting", False)

    if problems and not was_alerting:
        message = (
            "\n".join(f"• {p}" for p in problems)
            + f"\n\nProduction du jour : {today_text}"
            + f"\nPuissance instantanée : " + describe_power(snap["per_ecu"])
            + "\n\n👉 Vérifiez les disjoncteurs PV dans le garage, "
              "puis l'alimentation et le réseau de l'ECU."
        )
        if not dry_run:
            notify("🔴 Alerte photovoltaïque", message)
        state["alerting"] = True
        state["alert_since"] = now.isoformat()
        log(summary + " | ALERTE ENVOYÉE " + message)

    elif not problems and was_alerting:
        since = state.get("alert_since", "?")
        if not dry_run:
            notify(
                "✅ Photovoltaïque : retour à la normale",
                f"La production a repris.\nPuissance : "
                + describe_power(snap["per_ecu"])
                + f"\nIncident ouvert depuis : {since}",
                urgent=False,
            )
        state["alerting"] = False
        state.pop("alert_since", None)
        log(summary + " | RETOUR À LA NORMALE")

    elif problems:
        log(summary + " | Problème toujours présent — pas de nouvelle notification (anti-spam).")
    else:
        log(summary + " | Tout va bien.")

    state["last_check"] = now.isoformat()
    save_state(state)

    # --- 5. Dead man's switch ---------------------------------------------- #
    # Si CE script cesse de tourner (machine éteinte, box HS, cron cassé),
    # Healthchecks.io vous alertera de son côté. C'est la sécurité qui manque
    # à toute surveillance reposant uniquement sur le cloud du fabricant.
    if HEALTHCHECKS_URL and not dry_run:
        try:
            urllib.request.urlopen(HEALTHCHECKS_URL, timeout=10).read()
        except Exception as exc:  # noqa: BLE001
            log(f"[healthchecks] ping échoué : {exc}", err=True)

    return 0


def main() -> int:
    if "--test" in sys.argv:
        ok = notify(
            "🔔 Test — surveillance photovoltaïque",
            "Si vous lisez ceci, vos notifications fonctionnent.",
            urgent=False,
        )
        return 0 if ok else 3

    missing = [k for k, v in
               (("APS_APP_ID", APP_ID), ("APS_APP_SECRET", APP_SECRET), ("APS_SID", SID))
               if not v]
    if missing:
        log("Variables manquantes : " + ", ".join(missing), err=True)
        return 2

    if not (NTFY_TOPIC or (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID) or SMTP_HOST):
        log("[!] Aucun canal de notification configuré : les alertes ne partiront "
            "nulle part. Définissez NTFY_TOPIC, TELEGRAM_* ou SMTP_*.", err=True)

    return run(dry_run="--dry-run" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
