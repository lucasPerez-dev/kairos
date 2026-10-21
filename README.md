# 🎢 Kairos

Copilote de files d'attente pour un week-end à **PortAventura World** (PortAventura Park + Ferrari Land).

Kairos interroge en continu les temps d'attente, apprend la *tendance* de chaque file, et vous dit **où aller maintenant** — via un bot Telegram et un dashboard web.

> Données : **Powered by [Queue-Times.com](https://queue-times.com/en-US)** (API publique, temps d'attente en direct, rafraîchis toutes les ~5 min).

---

## Fonctionnement

```
queue-times.com ──5 min──▶ SQLite ──▶ moteur de score ──▶ bot Telegram
                                                 └──────▶ dashboard web
```

Le score d'une attraction combine :

```
valeur = priorité (×22 min)         # 3 = incontournable, 2 = à faire, 1 = si le temps
       + bonus de phase (journée)   # ouverture / heures pleines / dernière heure
       − temps d'attente
       − temps de marche depuis ta zone
       −/＋ tendance de la file      # la file qui se vide est un bon pari
       + urgence avant la fermeture
```

Le moteur s'appuie sur un **modèle de marche grossier entre zones** (`config/zones.json`) et un **historique de snapshots** pour calculer la tendance.

---

## Installation

Prérequis : **Python 3.11+**.

```bash
cd parkTracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # + requirements-dev.txt pour les tests
```

## Configuration

```bash
cp .env.example .env
```

Éditer `.env` :

| Variable | Rôle |
|---|---|
| `KAIROS_TELEGRAM_BOT_TOKEN` | Token du bot (via [@BotFather](https://t.me/BotFather)). Vide = bot désactivé (dashboard seul). |
| `KAIROS_TELEGRAM_CHAT_ID` | ID du groupe. Optionnel : envoyez `/id` au bot pour le connaître. |
| `KAIROS_MODE` | `collect` (collecte silencieuse) ou `trip` (alertes actives). |
| `KAIROS_POLL_INTERVAL_SECONDS` | Fréquence de sondage (défaut 300). |
| `KAIROS_ALERT_DEFAULT_THRESHOLD_MINUTES` | Seuil par défaut des alertes. |
| `KAIROS_SKIP_WHEN_CLOSED` | Ne pas sonder quand le parc est fermé (défaut `true`) : évite de stocker des données inutiles. |

Autres fichiers de configuration, éditables :

- `config/rides.json` — les 60 attractions (id, nom, parc, zone, catégorie, priorité)
- `config/zones.json` — modèle de marche entre zones
- `config/hours.json` — horaires d'ouverture **par parc**. Résolution `closed` > `days[date]` > `weekly[jour]` > `default`, avec gestion des nocturnes (fermeture ≤ ouverture lue comme « après minuit »). *(PortAventura et Ferrari Land ont des horaires différents — à ajuster selon la saison.)*

## Lancement

```bash
python main.py
```

- Bot Telegram en *long polling* (aucun port à exposer).
- Dashboard sur `http://localhost:8080`.

---

## Commandes du bot

| Commande | Effet |
|---|---|
| `/ou` | Où aller maintenant (top 3 avec justification) |
| `/etat [parc]` | Temps d'attente en direct, par parc/zone |
| `/zone <nom>` | Indiquer sa zone actuelle (boutons disponibles) |
| `/liste` | Toutes les attractions et leurs priorités |
| `/fait <attraction>` · `/annule <attraction>` | Cocher / décocher une attraction (partagé) |
| `/prio <attraction> <1-3>` | Régler la priorité |
| `/ajouter` · `/retirer` | Raccourcis de priorité (3 / 1) |
| `/seuil [minutes]` | Voir/régler le seuil d'alerte global ou par attraction |
| `/alertes [toutes\|coasters]` | Portée des notifications : toutes les attractions ou seulement les montagnes russes |
| `/alertes sensibilite prudent\|normal\|agressif` | Sensibilité de l'alerting adaptatif (défaut : agressif) |
| `/masquer <attraction>` · `/afficher <attraction>` | Masquer (attraction fantôme) ou réafficher une attraction |
| `/parc` | Attente moyenne par parc |
| `/mode collect\|trip` | Basculer le mode de fonctionnement |
| `/refresh` | Forcer une mise à jour immédiate |
| `/stats` | État du service et de la collecte |
| `/test` | Envoyer une notification de test |
| `/id` | Identifiant du chat courant |

**Alertes automatiques** (mode `trip`) : l'alerting est **adaptatif**. Pour chaque attraction et chaque heure, le bot apprend l'attente habituelle (fenêtre glissante de 21 jours) et prévient quand elle devient **anormalement basse pour cette heure**, et seulement si l'attraction est normalement demandée à ce moment-là. Résultat : plus de « 0 min » inutile sur les manèges toujours vides, et un seuil propre à chaque coaster sans réglage manuel.

- **Sensibilité** : `prudent` / `normal` / `agressif` (défaut), via `/alertes sensibilite …` ou le bouton ⚙️ Réglages.
- **Portée** : `/alertes toutes` (défaut) ou `/alertes coasters`.
- **Démarrage à froid** (historique insuffisant) : repli sur un seuil manuel (`/seuil`).
- Anti-spam : cooldown 30 min, briefing le matin, rappel 1 h avant la fermeture.

## Panneau interactif

`/menu` ouvre un **panneau de bord qui évolue sur place** : chaque bouton met à jour le même message. Les boutons sont cliquables par **tous les membres du groupe**.

- 🎯 **Où aller** — top 3, avec un bouton « ✅ » pour cocher chaque attraction
- 📊 **Temps d'attente** — filtrable par parc
- 📍 **Ma zone** — sélection de zone (affine les temps de marche)
- ✅ **Checklist** — liste paginée, on coche/décoche d'un simple tap
- ⚙️ **Réglages** — sensibilité des alertes (prudent / normal / agressif), bascule collecte ↔ séjour, portée (toutes / coasters), notification de test
- ♻️ **Actualiser** — force un poll immédiat sans taper `/refresh`

> **Pourquoi des boutons inline et pas une reply keyboard ?** Dans un groupe, Telegram ne transmet pas au bot les messages issus d'une reply keyboard (mode confidentialité). Les boutons inline, eux, passent toujours (callback queries) — et restent partagés par tout le groupe.

---

## Dashboard

Page web auto-rafraîchie (60 s) accessible en LAN **et via Tailscale**, pensée **mobile-first** :

- un **héros** « Meilleur choix » (attente, tendance, marche, raison) + les alternatives en pastilles ;
- un **board compact** par parc, avec **barre proportionnelle** et couleur pilotée par le seuil d'alerte réel (pas de seuils codés en dur) ;
- **onglet 🎢 Coasters** : ne montre que les montagnes russes, avec un héros « Meilleur coaster » (scoring dédié) ;
- **filtres** (Toutes / Ouvertes / Priorité 3), **tri** (attente / priorité / zone) et onglets par parc ;
- attractions **fermées repliées** dans un bloc dépliable ;
- **pastilles de phase par parc**, **indicateur de fraîcheur** (« maj il y a X s »), **bouton ♻️ Actualiser** (poll immédiat) ;
- **banderole** de rappel quand les horaires sont estimés/non vérifiés.

Fichier unique (HTML inline + JS vanilla, aucun build). Endpoints : `GET /`, `GET /api/state`, `GET /api/ride/{id}`, `POST /api/refresh`, `POST /api/zone`, `GET /healthz`. Les images sont servies depuis `/static/rides/`.

## Fiches attraction

Un **tap sur une ligne** du board ouvre une fiche (bottom-sheet) : photo, faits (catégorie, zone, parc, priorité), attente en direct, **profil horaire** (attente moyenne par heure, calculée depuis l'historique) et « meilleur créneau », plus une courte description.

- **Images** : `static/rides/<id>.<ext>` (id queue-times). Commitées dans git. Absentes → l'icône de catégorie s'affiche.
- **Descriptions** : `config/rides_descriptions.json` (id → texte).
- **Crédits photo** : `config/rides_credits.json` (id → auteur / licence / source).
- **Outil** : `python tools/rides_media.py report` (liste les manquants) puis `fetch` (récupère les images manquantes depuis Wikimedia Commons et remplit les crédits).

---

## Déploiement (Proxmox + Tailscale)

### 1. Conteneur LXC Debian

```bash
# Sur l'hôte Proxmox
pct create 200 local:vztmpl/debian-12-standard_*.tar.zst \
  --hostname kairos --memory 512 --cores 1 --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1
```

### 2. Installation dans le conteneur

**Automatique** (recommandé) — depuis la racine du projet, en root :

```bash
git clone <votre-depot> /opt/kairos && cd /opt/kairos
bash deploy/install.sh
nano .env                 # colle le token Telegram
systemctl start kairos
journalctl -u kairos -f   # vérifie « Bot Telegram démarré »
```

`install.sh` crée le venv, installe les dépendances, crée l'utilisateur de service, génère `.env` (mode 600), installe l'unit systemd et le **cron de sauvegarde quotidienne**.

**Manuelle** (équivalent) :

```bash
apt update && apt install -y python3-venv python3-pip git
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
useradd -r -s /usr/sbin/nologin kairos
chmod 600 .env && chown -R kairos:kairos /opt/kairos
cp deploy/kairos.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now kairos
```

### 3. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

Le dashboard devient accessible dans le tailnet sur `http://<hostname-tailnet>:8080`. Aucun port n'est exposé publiquement.

### 4. Sauvegarde

Automatique si tu es passé par `deploy/install.sh` (cron quotidien à 04:00). Sinon :

```bash
# /etc/cron.d/kairos-backup
0 4 * * * kairos APP_DIR=/opt/kairos /opt/kairos/deploy/backup.sh >/dev/null 2>&1
```

`backup.sh` utilise l'API sqlite3 de Python (pas besoin du binaire `sqlite3`) et fait tourner les sauvegardes (60 conservées par défaut, `KEEP` pour ajuster).

---

## Maintenance

- **Nouvelles attractions** : `python tools/refresh_rides.py` met à jour `config/rides.json` en conservant zones/priorités existantes (à relancer avant le séjour).
- **Horaires** : corriger `config/hours.json` dès que le parc publie les horaires définitifs d'avril 2027.
- **Vérifier la config** : `python tools/check_config.py` (n'affiche jamais le token en clair).
- **Auditer les attractions** : `python tools/audit_rides.py` lit l'historique et propose
  les attractions fantômes à masquer, les priorités et seuils à ajuster (commandes prêtes à copier).
- **Fiches (images/descriptions)** : `python tools/rides_media.py report` puis
  `python tools/rides_media.py fetch` (images depuis Wikimedia Commons, crédits automatiques).
- **Backtest des alertes** : `python tools/backtest_alerts.py --coasters-only --detail`
  rejoue l'historique et compare l'alerting fixe et l'adaptatif, par sensibilité.
- **Tests** : `pytest`.

## Dépannage

- **`ModuleNotFoundError: pydantic_settings`** → tu lances avec le Python du système. Active le venv (`source .venv/bin/activate`) ou utilise `.venv/bin/python tools/check_config.py`.
- **`telegram.error.TimedOut` au démarrage** → réseau résolvant Telegram en IPv6 alors que l'IPv6 ne répond pas. Kairos force déjà l'IPv4 par défaut (`KAIROS_FORCE_IPV4=true`), avec timeouts larges et 3 tentatives ; le dashboard et la collecte continuent même si le bot ne peut pas démarrer.
- **Le bot ne reçoit rien dans le groupe** → vérifie qu'il est bien membre du groupe ; si `/id` ne répond pas, utilise `curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates"`.
- **Aucune alerte** → le mode `collect` désactive les alertes : passe en `trip` (`/mode trip` ou `KAIROS_MODE=trip`).

## Notes & limites

- Granularité de **5 minutes** (fréquence de l'API) : parfait pour décider « j'y vais maintenant », pas pour du temps réel à la seconde.
- Les temps affichés par les parcs sont parfois **surestimés de 5–15 %** : les recommandations sont donc légèrement pessimistes.
- `zone` et `category` des attractions sont des **estimations** (queue-times ne les fournit pas) : elles n'affectent que l'ordre des recommandations et peuvent être ajustées dans `config/rides.json`.
- En mode `collect` (dès maintenant), Kairos accumule l'historique pour, à terme, proposer des **prédictions** par heure/jour — utile pour départager le 2, 3 et 4 avril.
