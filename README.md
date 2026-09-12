# Surveillance photovoltaïque APsystems — installation sur Synology DS925+

Conteneur Docker qui interroge l'OpenAPI APsystems toutes les 30 minutes entre
7h00 et 22h00, et vous notifie si la production s'arrête (disjoncteur qui a
sauté, ECU hors ligne, onduleur en alarme).

---

## Prérequis

- NAS Synology avec DSM 7.2 ou supérieur
- Le paquet **Container Manager** installé (Centre de paquets)
- Vos identifiants **OpenAPI APsystems** (`App Id`, `App Secret`, `sid`), à 
  demander directeemnt sur le site web APsystems.
  **Faites cette démarche en premier** : l'accès n'est pas automatique et peut
  être facturé.

---

## Étape 1 — Déposer les fichiers sur le NAS

Container Manager crée un dossier partagé `docker` à son installation.
Via **File Station**, créez le dossier `docker/pv-watchdog` et déposez-y :

```
/volume1/docker/pv-watchdog/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── apsystems_watchdog.py
└── config.env          <- à créer à partir de config.env.example
```

## Étape 2 — Configurer

Renommez `config.env.example` en `config.env` et remplissez-le (éditable
directement dans File Station : clic droit → Ouvrir avec l'éditeur de texte).

Au minimum :

```
APS_APP_ID=votre_app_id
APS_APP_SECRET=votre_secret
APS_SID=votre_sid
NTFY_TOPIC=pv-garage-xxxxxxxx
```

Pour le sujet ntfy, prenez quelque chose d'imprévisible (il est public par
construction) et abonnez-y l'application ntfy sur votre téléphone.

> **Attention à la syntaxe** : dans un `env_file`, on écrit `CLE=valeur`.
> Pas de `export`, pas de guillemets, pas d'espace autour du `=`.
> Une valeur entre guillemets serait lue *avec* les guillemets.

## Étape 3 — Créer le projet dans Container Manager

1. Ouvrez **Container Manager** → onglet **Projet** → **Créer**
2. Nom du projet : `pv-watchdog`
3. Chemin : `/volume1/docker/pv-watchdog`
4. Source : **Utiliser le fichier docker-compose.yml existant**
5. Cliquez sur **Suivant** puis **Terminé**

DSM construit l'image (une à deux minutes la première fois) et démarre le
conteneur.

## Étape 4 — Vérifier

Dans Container Manager → Projet → `pv-watchdog` → onglet **Journal**, vous
devez voir l'en-tête de démarrage, puis une notification de test arriver sur
votre téléphone dans la foulée.

Ensuite, à chaque exécution :

```
2026-09-01T20:08:34 Soleil : 1.3° | Voyant : 1 (Vert — fonctionnement normal) | Aujourd'hui : 34.71 kWh | ECUs : 215000042894 = 194.0 W à 20:00 (il y a 9 min) | Tout va bien.
```

Une fois validé, passez `PV_TEST_ON_START` à `0` dans `docker-compose.yml`
pour ne plus recevoir de notification à chaque redémarrage du NAS.

---

## Réglages courants

Tout se modifie dans `docker-compose.yml` (section `environment`), puis
**Projet → Action → Reconstruire** :

| Variable              | Défaut       | Rôle                                    |
|-----------------------|--------------|-----------------------------------------|
| `PV_START_TIME`       | `07:00`      | Première exécution de la journée        |
| `PV_END_TIME`         | `22:00`      | Dernière exécution                      |
| `PV_INTERVAL_MINUTES` | `30`         | Cadence                                 |
| `TZ`                  | Europe/Paris | Fuseau (gère l'heure d'été/hiver)       |

Et dans `config.env` :

| Variable            | Défaut | Rôle                                          |
|---------------------|--------|-----------------------------------------------|
| `PV_MIN_ELEVATION`  | `15`   | Hauteur du soleil au-delà de laquelle on attend de la production. **Descendez à 10 en hiver** : à Lyon le soleil culmine vers 20° au solstice. |
| `PV_GRACE_MINUTES`  | `60`   | Durée de production nulle avant d'alerter     |
| `PV_MIN_POWER_W`    | `20`   | Seuil sous lequel on considère la production nulle |
| `PV_QUOTA_PROBE_HOUR` | `12` | Heure de l'unique essai quotidien quand le quota d'appels est épuisé |

---

## Quand le quota d'appels APsystems est épuisé

L'OpenAPI est facturée à l'usage et plafonnée : une fois le quota consommé,
elle répond `code 2005` à *tous* les appels, et plus rien n'est observable.
Continuer à interroger toutes les 30 minutes ne ferait qu'entamer le quota du
lendemain et remplir le journal d'erreurs.

Le script :

1. vous notifie **une seule fois** (ntfy / Telegram / e-mail) que la
   surveillance est suspendue faute de quota ;
2. se met en veille et ne retente **qu'un appel par jour, à midi** ;
3. vous notifie dès que l'appel repasse, et reprend sa cadence normale.

Le ping Healthchecks.io continue pendant la veille : le script est bien vivant,
c'est l'API qui ne répond plus — le dead man's switch ne doit pas se déclencher
pour ça.

---

## Le point le plus important : le dead man's switch

Ce conteneur surveille vos panneaux. **Mais qui surveille le conteneur ?**

Si le NAS s'éteint, si la fibre tombe, si le projet Docker plante après une
mise à jour DSM — le conteneur se tait, et un silence ressemble exactement à
« tout va bien ». C'est précisément le mode d'échec qui vous a coûté deux
semaines de production.

Solution, gratuite et en trois minutes :

1. Créez un compte sur **healthchecks.io**
2. Nouveau check, période **30 minutes**, délai de grâce **90 minutes**
3. Copiez l'URL de ping dans `config.env` :
   `HEALTHCHECKS_URL=https://hc-ping.com/xxxxxxxx-xxxx-xxxx`

Le script pinge cette URL à chaque exécution réussie. Si les pings s'arrêtent,
healthchecks.io vous envoie un e-mail. Vous êtes alors prévenu d'une panne de
votre surveillance, et pas seulement d'une panne de vos panneaux.

---

## Dépannage

| Symptôme dans le journal | Cause probable |
|---|---|
| `OpenAPI code 3002 (Signature invalide)` | Ajoutez `APS_SIGN_FULL_PATH=1` dans `config.env`. Le manuel définit le champ à signer comme « le dernier segment du chemin », ce qui est ambigu — ce drapeau bascule sur le chemin complet. |
| `OpenAPI code 2002 / 2004` | Compte OpenAPI non autorisé sur cette catégorie de données. À voir avec APsystems. |
| `OpenAPI code 2005 (Quota d'appels dépassé)` | Le script vous notifie une fois, puis se met en veille et ne retente qu'un appel par jour, à midi, jusqu'au renouvellement du quota (voir ci-dessous). Pour éviter que cela se reproduise, augmentez `PV_INTERVAL_MINUTES` à 60. |
| `OpenAPI code 7001 / 7002` | Limite de débit momentanée. Sans gravité si c'est isolé ; sinon espacez les relevés. |
| `OpenAPI code 1001 (Aucune donnée)` | `APS_SID` incorrect, ou l'ECU n'a jamais remonté de données. |
| `[!] AUCUN canal de notification n'a fonctionné` | `config.env` vide, mal orthographié, ou syntaxe avec guillemets/espaces. |
| Alertes en pleine nuit | `TZ` mal pris en compte. Vérifiez la ligne « il est HH:MM » au démarrage du journal. |
| Fausses alertes en hiver | Baissez `PV_MIN_ELEVATION` à 10 et montez `PV_GRACE_MINUTES` à 90. |
| Onglet **Journal** vide alors que le conteneur tourne | Une section `logging:` dans `docker-compose.yml` détourne la sortie vers un pilote que DSM ne lit pas. Retirez-la, puis **supprimez et recréez** le conteneur : le pilote de journalisation est figé à sa création, un simple redémarrage ne suffit pas. |

Pour tester la chaîne de notification sans attendre :
**Container Manager → Conteneur → pv-watchdog → Terminal → Créer**, puis :

```
python3 /app/apsystems_watchdog.py --test
python3 /app/apsystems_watchdog.py --dry-run
```

`--dry-run` affiche l'état réel de l'installation sans envoyer d'alerte : c'est
le meilleur moyen de vérifier que vos identifiants API fonctionnent.

---

## Licence

Ce projet est distribué sous licence **GNU Affero General Public License v3.0
ou ultérieure** (AGPL-3.0-or-later). Le texte complet se trouve dans le fichier
[LICENSE](LICENSE).

Concrètement, si vous forkez ou modifiez ce code :

- vous devez publier votre version modifiée sous la même licence AGPL ;
- cela vaut aussi si vous ne distribuez pas le programme mais le proposez
  comme service accessible par le réseau (clause réseau de l'article 13) ;
- vous devez conserver les mentions de copyright et indiquer vos modifications.

Copyright (C) 2026 Sébastien Galtier.
