#!/bin/sh
# -----------------------------------------------------------------------------
# Planificateur interne du conteneur.
#
# Pourquoi pas cron ? Parce que cron dans un conteneur impose de recopier
# l'environnement à la main (cron ne voit pas les variables du conteneur),
# gère mal les logs, et complique le fuseau horaire. Une boucle sh fait le
# travail en 20 lignes, écrit directement sur la sortie standard (donc visible
# dans Container Manager), et se cale exactement sur les minutes 00 et 30.
# -----------------------------------------------------------------------------

set -eu

START_TIME="${PV_START_TIME:-07:00}"   # première exécution de la journée
END_TIME="${PV_END_TIME:-22:00}"       # dernière exécution de la journée
INTERVAL="${PV_INTERVAL_MINUTES:-30}"  # cadence, en minutes

to_minutes() {
    # "07:30" -> 450
    # Le /bin/sh de Debian est dash, pas bash : la syntaxe 10# n'existe pas et
    # "08"/"09" seraient lus comme de l'octal invalide. On retire donc les
    # zéros de tête à la main (et "00" devenu vide retombe sur 0).
    h=$(echo "$1" | cut -d: -f1 | sed 's/^0*//')
    m=$(echo "$1" | cut -d: -f2 | sed 's/^0*//')
    echo $(( ${h:-0} * 60 + ${m:-0} ))
}

START_MIN=$(to_minutes "$START_TIME")
END_MIN=$(to_minutes "$END_TIME")
STEP=$(( INTERVAL * 60 ))

echo "=========================================================="
echo " Surveillance photovoltaïque APsystems"
echo " Fuseau      : ${TZ:-UTC}  (il est $(date '+%H:%M'))"
echo " Plage       : ${START_TIME} -> ${END_TIME}"
echo " Cadence     : toutes les ${INTERVAL} min"
echo " État        : ${PV_STATE_FILE:-~/.apsystems_watchdog.json}"
echo "=========================================================="

# Test de notification au premier démarrage : on vérifie tout de suite que la
# chaîne d'alerte fonctionne, plutôt que de le découvrir le jour de la panne.
if [ "${PV_TEST_ON_START:-1}" = "1" ]; then
    echo "[$(date '+%F %T')] Envoi d'une notification de test..."
    python3 /app/apsystems_watchdog.py --test || \
        echo "[!] Le test de notification a échoué — vérifiez la configuration."
fi

while true; do
    # %-H / %-M : date GNU supprime le zéro de tête, ce qui évite l'octal.
    now_min=$(( $(date +%-H) * 60 + $(date +%-M) ))

    if [ "$now_min" -ge "$START_MIN" ] && [ "$now_min" -le "$END_MIN" ]; then
        echo "----- [$(date '+%F %T')] -----"
        # On ne veut jamais que la boucle meure : une erreur du script ne doit
        # pas arrêter la surveillance des heures suivantes.
        python3 /app/apsystems_watchdog.py || \
            echo "[!] Exécution terminée en erreur (code $?)"
    fi

    # Sommeil calé sur le prochain multiple de l'intervalle (xx:00, xx:30...)
    now_epoch=$(date +%s)
    next=$(( (now_epoch / STEP + 1) * STEP ))
    sleep $(( next - now_epoch ))
done
