# Ou court le club ? 🏃‍♂️🗺️

Carte interactive des courses ou les membres du club **[Run Event 86](https://www.facebook.com/RunEvent86/)** (Vienne, 86) sont inscrits.

**[Voir la carte](https://juulieen.github.io/ou-court-le-club/)**

[![Voir la carte](docs/img/screenshot.png)](https://juulieen.github.io/ou-court-le-club/)

## Comment ca marche ?

Un pipeline de scrapers parcourt chaque jour les listes d'inscription de **19 plateformes** de courses a pied en France et a l'international. Il detecte les membres du club via deux methodes :

1. **Par club** — regex sur le champ "club" des inscriptions (ex: "Run Event 86", "RunEvent", etc.)
2. **Par nom** — liste de membres connus pour ceux qui n'ont pas rempli le champ club

Les resultats sont affiches sur une carte MapLibre GL avec les tuiles MapTiler. Les prenoms des membres qui ont donne leur consentement sont affiches a cote des courses.

## Plateformes supportees (19)

### Plateformes principales (14 scrapers)

| Plateforme | Courses typiques | Methode |
|---|---|---|
| **Klikego** | Trail des Chateaux Chauvigny, Trail de Pons | AJAX POST + recherche par club |
| **Njuko** | Marathon Poitiers, EcoTrail, Veni Vici | API REST + cache de slugs |
| **OnSinscrit** | Techno Trail, Foulees du Maraisthon | HTML scraping |
| **Protiming** | Trail des Chateaux de la Loire | Filtre club en URL |
| **Chronometrage.com** | Corrida, courses regionales | Next.js JSON |
| **Chrono-Start** | Trail de la Cascade d'Ars, Course d'Enfert | HTML + cloudscraper (Cloudflare) |
| **3wsport** | Courses departementales | HTML scraping |
| **Espace-Competition** | Courses regionales | HTML pagine |
| **Sportips** | Maxi-Race Annecy | API JSON recherche par nom |
| **TimePulse** | La Demoniak, Foulee des Geants | HTML scraping |
| **Endurance Chrono** | Courses Sud-Ouest | HTML tri par club |
| **Listino** | Courses regionales | HTML pagine |
| **IPITOS** | Semi Orvault, Foulees Angouleme, Defi Colline | XML .clax via live.ipitos.com |
| **RunChrono** | Courses locales dept 86 | Decouverte -> OnSinscrit |

### White-labels Njuko (5 plateformes via la meme API)

Plusieurs plateformes utilisent l'API Njuko sous le capot. Le scraper Njuko les gere toutes :

| Plateforme | Domaine | Courses typiques |
|---|---|---|
| **Njuko** (defaut) | njuko.com | Marathon Poitiers, EcoTrail Paris |
| **UTMB** | register-utmb.world | Nice by UTMB, Trail Alsace |
| **Sporkrono** | sporkrono-inscriptions.fr | L'Epopee Royale |
| **Sports107** | sports107.com | SainteLyon |
| **timeto** | timeto.com | Marathon de Paris (ASO) |

### Plateforme decouverte-seulement

| Plateforme | Raison |
|---|---|
| **HelloAsso** | Les listes de participants sont **privees par design**. Les courses HelloAsso (ex: Tic Tac Trail) doivent etre ajoutees manuellement dans `config.yml`. |

## Plateformes non supportees

Certaines courses utilisent des plateformes que le projet ne peut pas scanner :

| Plateforme | Exemple de course | Raison |
|---|---|---|
| **Inscription sur place** | Zombi'run (Chatellerault) | Pas d'inscription en ligne. Inscription le jour J uniquement. |
| **Site propre (custom)** | Semi-marathon Niort (coulee verte) | L'organisateur utilise son propre site (semi-marathon-niort.com) sans liste publique. Cependant, les resultats apparaissent sur IPITOS apres la course. |
| **Plateforme inconnue** | Trail Haut Val de Sevres | Inscription pas encore ouverte, plateforme non identifiee. |

> **Note :** Si ta course utilise une plateforme non supportee, tu peux l'ajouter manuellement dans `config.yml` (voir FAQ).

## Fonctionnalites

- **Carte interactive** avec marqueurs, clusters, popups multi-editions
- **Prenoms opt-in** : les membres qui ont donne leur consentement voient leur prenom affiche (RGPD art. 6.1.a)
- **Filtre par membre** : dropdown pour voir les courses d'un membre specifique ou "Autres membres" (anonymes)
- **Filtres** : a venir / recentes (3 mois) / toutes, type (trail/route), distance, dates
- **Stats en direct** : courses a venir, ce mois, nombre de coureurs uniques
- **Mobile-first** : sidebar draggable (bottom sheet) avec 3 positions, filtres pliables, legende en overlay
- **Desambiguisation** automatique des prenoms doublons (ex: "Romain F." / "Romain R.")

## Stack technique

- **Frontend** : MapLibre GL JS + MapTiler (outdoor-v2) + HTML/CSS/JS statique
- **Backend** : Python (requests, BeautifulSoup, cloudscraper, xml.etree)
- **Geocoding** : API BAN (primaire) + Nominatim (fallback), 30+ OVERRIDES manuels
- **Hebergement** : GitHub Pages (deploiement via GitHub Actions artifact — les prenoms ne sont jamais commites)
- **CI/CD** : GitHub Actions (scraping quotidien a 6h UTC)

## Installation locale

```bash
# Cloner le repo
git clone https://github.com/juulieen/ou-court-le-club.git
cd ou-court-le-club

# Creer le fichier de config (voir config.example.yml)
cp config.example.yml config.yml
# Editer config.yml avec les patterns du club et les noms des membres

# Installer les dependances
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Lancer le scraper
python -m scrapers.main

# Voir la carte
# Ouvrir docs/index.html dans un navigateur
```

## Configuration

Copier `config.example.yml` en `config.yml` et remplir :

- `club.patterns` — expressions regulieres pour matcher le nom du club
- `club.known_members` — liste des membres connus (format "NOM Prenom")
- `club.display_optin` — membres ayant consenti a l'affichage de leur prenom (RGPD)
- `races` — courses manuelles (optionnel, pour les plateformes non supportees)

## FAQ

**Pourquoi ma course n'apparait pas sur la carte ?**

Plusieurs raisons possibles :

- **La course est passee et le projet n'existait pas encore** — Le scraper ne decouvre que les evenements *a venir*. Les courses passees dont les listes d'inscrits ont ete desactivees sont invisibles. Tu peux les ajouter manuellement dans `config.yml` (section `races`).
- **Le champ club n'est pas rempli (ou mal rempli)** — Si tu n'as pas mis "Run Event 86" (ou une variante) dans le champ club a l'inscription, le scraper ne te trouvera que si ton nom est dans la liste `known_members` de `config.yml`.
- **La plateforme n'est pas supportee** — Seules 19 plateformes sont scannees (voir tableau ci-dessus). Si ta course utilise une autre plateforme, elle ne sera pas detectee.
- **La course est sur HelloAsso** — Les listes de participants HelloAsso sont privees. Les courses HelloAsso doivent etre ajoutees manuellement.
- **Probleme de geocoding** — La course est peut-etre detectee mais n'a pas pu etre placee sur la carte (coordonnees manquantes).

**Comment ajouter une course manuellement ?**

Ajouter une entree dans la section `races` de `config.yml` :

```yaml
races:
  - platform: manual
    name: "Nom de la course"
    date: "2026-01-25"
    location: "Ville, Departement"
    members:
      - name: "NOM Prenom"
        bib: "10km"
```

**Pourquoi plusieurs editions de la meme course (2024, 2025, 2026) apparaissent ?**

Les listes d'inscrits des annees precedentes restent accessibles sur certaines plateformes (Njuko notamment). Le scraper les detecte donc comme des courses actives. Sur la carte, elles sont regroupees en un seul marqueur avec un badge "×N ed." et une timeline dans le popup.

**Comment etre visible sur la carte ?**

Deux options :
1. Remplir correctement le champ "club" a l'inscription (ex: "Run Event 86")
2. Demander a l'administrateur d'ajouter ton nom dans `known_members` de `config.yml`

**Comment afficher mon prenom sur la carte ?**

Donner ton consentement a l'administrateur (message sur le groupe du club). Ton nom sera ajoute dans `display_optin` de `config.yml`. Tu peux demander le retrait a tout moment.

## Vie privee

Ce projet respecte le RGPD (base legale : consentement, art. 6.1.a) :

- **Prenoms uniquement** — seuls les prenoms des membres ayant donne leur consentement sont affiches. Aucun nom de famille n'est publie.
- **Pas dans Git** — le fichier `docs/data/races.json` (contenant les prenoms) est deploye via GitHub Actions artifact et **n'est jamais commite** dans le depot. Les prenoms n'apparaissent dans aucun historique Git.
- **Opt-in explicite** — les membres non listes dans `display_optin` sont comptes mais restent anonymes
- **Droit de retrait** — tout membre peut demander le retrait de son prenom a tout moment
- Le fichier `config.yml` (contenant les noms complets) est **gitignore** et stocke en secret GitHub
- Les donnees sont collectees a partir de listes d'inscription **publiques**

## Licence

MIT

---

*Projet personnel de [Julien OLLIVIER](https://github.com/juulieen), membre du club Run Event 86. Ce projet n'est pas une initiative officielle du club.*
