# Ou court le club ? 🏃‍♂️🗺️

Carte interactive des courses ou les membres du club **[Run Event 86](https://www.facebook.com/RunEvent86/)** (Vienne, 86) sont inscrits.

**[Voir la carte](https://juulieen.github.io/ou-court-le-club/)**

[![Voir la carte](docs/img/screenshot.png)](https://juulieen.github.io/ou-court-le-club/)

## Comment ca marche ?

Un pipeline de scrapers parcourt chaque jour les listes d'inscription de **19 plateformes** de courses a pied en France et a l'international. Il detecte les membres du club via deux methodes :

1. **Par club** — regex sur le champ "club" des inscriptions (ex: "Run Event 86", "RunEvent", etc.)
2. **Par nom** — liste de membres connus pour ceux qui n'ont pas rempli le champ club

Les resultats sont affiches sur une carte MapLibre GL avec les tuiles MapTiler.

## Plateformes scannees

| Plateforme | Methode |
|---|---|
| Klikego | AJAX POST + recherche par club |
| Njuko | API REST + cache de slugs |
| OnSinscrit | HTML scraping |
| Protiming | Filtre club en URL |
| Chronometrage.com | Next.js JSON |
| Chrono-Start | HTML + cloudscraper (Cloudflare) |
| 3wsport | HTML scraping par departement |
| Espace-Competition | HTML pagine |
| Sportips | API JSON / HTML ancien format |
| TimePulse | HTML scraping |
| Endurance Chrono | HTML tri par club |
| Listino | HTML pagine |
| RunChrono | Decouverte locale (dept 86) -> OnSinscrit |
| IPITOS | XML .clax via live.ipitos.com |
| UTMB | Via API Njuko (register-utmb.world) |
| Sporkrono | Via API Njuko (sporkrono-inscriptions.fr) |
| Sports107 / SainteLyon | Via API Njuko (sports107.com) |
| timeto / Marathon de Paris | Via API Njuko (timeto.com) |

## Stack technique

- **Frontend** : MapLibre GL JS + MapTiler (outdoor-v2) + HTML/CSS/JS statique
- **Backend** : Python (requests, BeautifulSoup, cloudscraper, xml.etree)
- **Geocoding** : API BAN (primaire) + Nominatim (fallback)
- **Hebergement** : GitHub Pages
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

## Vie privee

Ce projet respecte le RGPD :

- **Aucun nom** n'est publie sur le site — seul le nombre de membres par course est affiche
- Le fichier `config.yml` (contenant les noms) est **gitignore** et stocke en secret GitHub
- Les donnees sont collectees a partir de listes d'inscription **publiques**

## Licence

MIT

---

*Projet personnel de [Julien OLLIVIER](https://github.com/juulieen), membre du club Run Event 86. Ce projet n'est pas une initiative officielle du club.*
