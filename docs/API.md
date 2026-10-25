# API fillinges-weather

Guide destiné aux services qui consomment la météo actuelle de la maison depuis un autre conteneur du NAS Synology DS925+.

- URL de base : `http://fillinges-weather:8000`
- Protocole : HTTP, réponses JSON (UTF-8), méthode `GET` uniquement, aucun paramètre.
- Authentification : aucune. Le service n'est joignable que depuis le réseau Docker `fillinges-weather`.
- Documentation interactive générée par FastAPI : `/docs` (Swagger UI) et `/openapi.json`.

## Raccorder un conteneur au service

Le service crée un réseau Docker nommé `fillinges-weather`. Un conteneur consommateur s'y raccorde en le déclarant comme réseau **externe** dans son propre `docker-compose.yml` ; le nom `fillinges-weather` est alors résolu par le DNS interne de Docker, quelle que soit l'adresse IP du conteneur après un redémarrage.

```yaml
services:
  mon-service:
    image: mon-service:latest
    environment:
      WEATHER_API_URL: http://fillinges-weather:8000
    networks:
      - default
      - fillinges-weather

networks:
  fillinges-weather:
    external: true
```

Garder `default` dans la liste si le service communique aussi avec d'autres conteneurs de son propre projet.

Points d'attention :

- **Ordre de démarrage** : le projet `fillinges-weather` doit être lancé avant, sinon Docker refuse de démarrer le consommateur (réseau externe introuvable). Il n'est pas nécessaire de le relancer ensuite : le réseau persiste tant que le projet `fillinges-weather` n'est pas supprimé.
- **Conteneurs en `network_mode: host`** (ou applications hors Docker) : ils ne peuvent pas rejoindre le réseau. Il faut alors publier le port en ajoutant `ports: ["8000:8000"]` au service dans [docker-compose.yml](../docker-compose.yml) et appeler `http://<ip-du-nas>:8000`. Le service n'ayant pas d'authentification, ne jamais ouvrir ce port vers Internet.
- Ne pas exposer l'URL `http://fillinges-weather:8000` à un navigateur : ce nom n'existe qu'à l'intérieur du réseau Docker.

## Routes

| Route | Usage |
|---|---|
| [`GET /current`](#get-current) | Météo actuelle fusionnée. **C'est la route à utiliser.** |
| [`GET /sources`](#get-sources) | État et dernières observations brutes de chaque source (diagnostic). |
| [`GET /health`](#get-health) | Vivacité du service et état de chaque source. |

En fonctionnement normal toutes les routes répondent `200`. Une erreur `500` signale un bug du service, pas une indisponibilité des sources.

## GET /current

Renvoie la météo actuelle au-dessus de la maison. La réponse est recalculée à chaque appel à partir des dernières observations en mémoire : l'appel est instantané et ne déclenche aucune requête vers les fournisseurs de données.

### Exemple de réponse

Valeurs illustratives.

```json
{
  "location": { "latitude": 46.16, "longitude": 6.35, "altitude_m": 600.0 },
  "generated_at": "2026-10-25T10:12:31.482915+00:00",
  "temperature": {
    "value_c": 7.4,
    "measured_c": 8.4,
    "altitude_correction_c": -1.0,
    "source": "meteofrance",
    "station": "74087001 Contamine-sur-Arve",
    "observed_at": "2026-10-25T10:00:00+00:00",
    "age_minutes": 12.5,
    "distance_km": 3.6,
    "others": [
      { "source": "meteofrance", "station": "74211002 Pers-Jussy", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 9.8, "value": 7.2 },
      { "source": "meteoswiss", "station": "GVE Genève / Cointrin", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 19.7, "value": 7.9 },
      { "source": "openmeteo", "station": "Open-Meteo meteofrance_seamless (AROME)", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 0.0, "value": 7.6 }
    ]
  },
  "humidity": {
    "value_percent": 92,
    "source": "meteofrance",
    "station": "74087001 Contamine-sur-Arve",
    "observed_at": "2026-10-25T10:00:00+00:00",
    "age_minutes": 12.5,
    "distance_km": 3.6
  },
  "wind": {
    "speed_kmh": 7.2,
    "gust_kmh": 14.4,
    "direction_deg": 230.0,
    "source": "meteofrance",
    "station": "74087001 Contamine-sur-Arve",
    "observed_at": "2026-10-25T10:00:00+00:00",
    "age_minutes": 12.5,
    "distance_km": 3.6,
    "others": [
      { "source": "meteofrance", "station": "74211002 Pers-Jussy", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 9.8, "value": 5.4 },
      { "source": "openmeteo", "station": "Open-Meteo meteofrance_seamless (AROME)", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 0.0, "value": 9.4 },
      { "source": "meteoswiss", "station": "GVE Genève / Cointrin", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 19.7, "value": 13.0 }
    ]
  },
  "precipitation": {
    "is_precipitating": true,
    "type": "rain",
    "rate_mm_h": 0.82,
    "wet_bulb_c": 6.6,
    "model_snowfall_cm_h": 0.0,
    "source": "radar",
    "station": "MeteoSwiss RZC rzc262981005rl.001.h5",
    "observed_at": "2026-10-25T10:05:00+00:00",
    "age_minutes": 7.5,
    "distance_km": 0.0,
    "others": [
      { "source": "meteofrance", "station": "74087001 Contamine-sur-Arve", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 3.6, "value": 0.6 },
      { "source": "meteofrance", "station": "74211002 Pers-Jussy", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 9.8, "value": 1.2 },
      { "source": "meteoswiss", "station": "GVE Genève / Cointrin", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 19.7, "value": 0.0 },
      { "source": "openmeteo", "station": "Open-Meteo meteofrance_seamless (AROME)", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 0.0, "value": 0.4 }
    ]
  },
  "sunshine": {
    "is_day": true,
    "is_sunny": false,
    "solar_elevation_deg": 30.0,
    "sunshine_fraction": null,
    "ghi_w_m2": 95.0,
    "dni_w_m2": 8.0,
    "clear_sky_index": 0.23,
    "source": "satellite",
    "station": "Open-Meteo satellite_radiation_seamless (EUMETSAT MTG)",
    "observed_at": "2026-10-25T09:50:00+00:00",
    "age_minutes": 22.5,
    "distance_km": 0.0,
    "others": [
      { "source": "meteoswiss", "station": "GVE Genève / Cointrin", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 19.7, "value": false },
      { "source": "openmeteo", "station": "Open-Meteo meteofrance_seamless (AROME)", "observed_at": "2026-10-25T10:00:00+00:00", "age_minutes": 12.5, "distance_km": 0.0, "value": false }
    ]
  },
  "attribution": [
    "Météo-France (Licence Ouverte Etalab 2.0)",
    "Source: MeteoSwiss (CC BY 4.0)",
    "Open-Meteo.com (CC BY 4.0), EUMETSAT / DWD satellite data"
  ]
}
```

### Conventions générales

- Toutes les dates sont en **UTC**, au format ISO 8601 avec décalage explicite (`+00:00`).
- Tout champ numérique peut valoir `null` : grandeur non mesurée par la source retenue, ou aucune source disponible.
- Quand aucune source n'est disponible pour une grandeur, le bloc est réduit à sa valeur principale (voir [Valeurs dégradées](#valeurs-dégradées)) : les champs de provenance et `others` sont alors **absents**, pas `null`.

### Champs de provenance

Présents dans chaque bloc dès qu'une valeur a été retenue, et dans chaque élément de `others` :

| Champ | Type | Description |
|---|---|---|
| `source` | string | `meteofrance`, `meteoswiss`, `radar`, `satellite` ou `openmeteo` |
| `station` | string | Identifiant et nom de la station, ou nom du produit (radar, satellite, modèle) |
| `observed_at` | string | Date de la mesure (UTC) |
| `age_minutes` | number | Âge de la mesure au moment de `generated_at` |
| `distance_km` | number | Distance entre la station et la maison ; `0.0` pour les données au point exact (radar, satellite, modèle) |

`others` liste les autres valeurs valides et assez récentes, dans l'ordre de priorité, avec un champ `value` (même unité que la valeur principale ; booléen pour `sunshine`). Il sert à juger la cohérence des sources, pas à remplacer la valeur principale.

### Bloc `location`

Position configurée de la maison : `latitude`, `longitude` (degrés WGS84), `altitude_m`.

### Bloc `temperature`

| Champ | Type | Description |
|---|---|---|
| `value_c` | number \| null | Température estimée à l'altitude de la maison (°C) |
| `measured_c` | number \| null | Température mesurée à la station (°C) |
| `altitude_correction_c` | number | Correction appliquée : `value_c - measured_c` |
| provenance, `others` | | Voir ci-dessus ; les valeurs de `others` sont aussi corrigées de l'altitude |

### Bloc `humidity`

| Champ | Type | Description |
|---|---|---|
| `value_percent` | integer \| null | Humidité relative (%), non corrigée de l'altitude |
| provenance | | Pas de `others` pour ce bloc |

### Bloc `wind`

| Champ | Type | Description |
|---|---|---|
| `speed_kmh` | number \| null | Vent moyen (km/h) |
| `gust_kmh` | number \| null | Rafale (km/h), de la même source que `speed_kmh` |
| `direction_deg` | number \| null | Direction d'où vient le vent (degrés, 0 = nord, 90 = est) |
| provenance, `others` | | Les valeurs de `others` sont des vitesses moyennes |

### Bloc `precipitation`

| Champ | Type | Description |
|---|---|---|
| `is_precipitating` | boolean \| null | `true` si l'intensité atteint 0,1 mm/h |
| `type` | string | `none`, `rain`, `snow`, `mixed` ou `unknown` |
| `rate_mm_h` | number | Intensité (mm/h) |
| `wet_bulb_c` | number \| null | Température humide utilisée pour déterminer la phase (°C) ; `null` sans précipitation |
| `model_snowfall_cm_h` | number \| null | Chute de neige prévue par AROME (cm/h), indication complémentaire |
| provenance, `others` | | Les valeurs de `others` sont des intensités en mm/h |

`type` vaut `unknown` quand aucune source de précipitation n'est disponible, ou qu'il précipite mais qu'aucune température n'est connue.

### Bloc `sunshine`

| Champ | Type | Description |
|---|---|---|
| `is_day` | boolean | Soleil au-dessus de l'horizon |
| `is_sunny` | boolean \| null | Ensoleillement direct (irradiance directe ≥ 120 W/m², ou à défaut fraction d'ensoleillement ≥ 50 %) ; toujours `false` la nuit |
| `solar_elevation_deg` | number | Hauteur du soleil (degrés, négative la nuit) |
| `sunshine_fraction` | number \| null | Part de la dernière période ensoleillée, 0 à 1 (MeteoSwiss uniquement) |
| `ghi_w_m2` | number \| null | Rayonnement global horizontal (W/m²) |
| `dni_w_m2` | number \| null | Rayonnement direct normal (W/m²) |
| `clear_sky_index` | number \| null | Rapport rayonnement global / rayonnement par ciel clair (satellite uniquement) ; proche de 1 par ciel dégagé |
| provenance, `others` | | Les valeurs de `others` sont des booléens `is_sunny` |

### Bloc `attribution`

Mentions de licence des données. Un service qui affiche ou redistribue ces données doit les reprendre.

### Valeurs dégradées

| Situation | Bloc renvoyé |
|---|---|
| Aucune température | `"temperature": { "value_c": null }` |
| Aucune humidité | `"humidity": { "value_percent": null }` |
| Aucun vent | `"wind": { "speed_kmh": null }` |
| Aucune précipitation | `"precipitation": { "is_precipitating": null, "type": "unknown" }` |
| Nuit | `"sunshine": { "is_day": false, "is_sunny": false, "solar_elevation_deg": -12.3 }` |
| Jour sans donnée d'ensoleillement | `"sunshine": { "is_day": true, "is_sunny": null, "solar_elevation_deg": 25.1 }` |

Ces cas se produisent notamment **dans les secondes qui suivent le démarrage** du conteneur, avant la première collecte, et lors d'une panne prolongée de toutes les sources d'une grandeur.

## GET /sources

Diagnostic : pour chaque source active, date du dernier succès, dernière erreur et observations brutes conservées. La source `meteofrance` n'apparaît que si une clé API est configurée.

```json
{
  "meteoswiss": {
    "last_success": "2026-10-25T10:20:04.118202+00:00",
    "last_error": null,
    "observations": [
      {
        "source": "meteoswiss",
        "label": "GVE Genève / Cointrin",
        "observed_at": "2026-10-25T10:00:00+00:00",
        "distance_km": 19.6983,
        "altitude_m": 411.0,
        "temperature_c": 9.1,
        "relative_humidity": 88.0,
        "wind_speed_kmh": 13.0,
        "wind_gust_kmh": 22.3,
        "wind_direction_deg": 220.0,
        "precipitation_rate_mm_h": 0.0,
        "snowfall_rate_cm_h": null,
        "sunshine_fraction": 0.0,
        "ghi_w_m2": 102.0,
        "dni_w_m2": null,
        "clear_sky_ghi_w_m2": null
      }
    ]
  },
  "radar": { "last_success": null, "last_error": "RuntimeError('No recent radar file found')", "observations": [] }
}
```

Une source en échec conserve ses dernières observations valides ; c'est l'âge maximal défini par source (voir le [README](../README.md#fonctionnement)) qui les écarte ensuite de `/current`. Les valeurs ne sont pas arrondies et le format de cette route peut évoluer : ne pas l'utiliser pour autre chose que du diagnostic.

## GET /health

```json
{
  "status": "ok",
  "sources": {
    "meteoswiss": true,
    "radar": false,
    "satellite": true,
    "openmeteo": true,
    "meteofrance": true
  }
}
```

- Une réponse `200` signifie que le service tourne. `status` vaut toujours `"ok"`.
- `sources.<nom>` vaut `true` si la dernière collecte de la source a réussi, `false` si elle a échoué ou n'a pas encore eu lieu.
- Une source à `false` ne rend pas forcément `/current` inutilisable : une autre source prend le relais.

## Bonnes pratiques côté client

- **Fréquence d'appel** : les sources se rafraîchissent toutes les 5 à 15 minutes ; interroger `/current` toutes les 1 à 5 minutes suffit. Un appel plus fréquent ne renvoie pas de données plus récentes.
- **Délai d'attente** : fixer un timeout court (quelques secondes) et conserver la dernière réponse valide en cas d'échec.
- **Valeurs nulles** : traiter chaque grandeur indépendamment et prévoir le cas `null` pour toutes, en particulier au démarrage du NAS.
- **Fraîcheur** : les mesures ont normalement de 5 à 30 minutes ; `age_minutes` permet d'appliquer un seuil plus strict si besoin.
- **Champs optionnels** : ne pas supposer la présence de `others` ni des champs de provenance ; ignorer les champs inconnus, de nouveaux champs pouvant être ajoutés.

### Exemple Python

```python
import httpx

WEATHER_API_URL = "http://fillinges-weather:8000"


def current_weather() -> dict:
    response = httpx.get(f"{WEATHER_API_URL}/current", timeout=5)
    response.raise_for_status()
    return response.json()


weather = current_weather()
temperature = weather["temperature"]["value_c"]
if temperature is not None:
    print(f"{temperature} °C ({weather['temperature']['station']})")
if weather["precipitation"]["type"] == "snow":
    print("Il neige")
```

### Exemple JavaScript (Node.js 18+)

```js
const WEATHER_API_URL = process.env.WEATHER_API_URL ?? "http://fillinges-weather:8000";

const response = await fetch(`${WEATHER_API_URL}/current`, { signal: AbortSignal.timeout(5000) });
if (!response.ok) throw new Error(`fillinges-weather: HTTP ${response.status}`);
const weather = await response.json();
console.log(weather.temperature.value_c, weather.sunshine.is_sunny);
```

### Exemple shell

```sh
curl -s http://fillinges-weather:8000/current | jq '.temperature.value_c, .precipitation.type'
```
