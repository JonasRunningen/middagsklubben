# 🍷 Middagsklubben

En privat loggbok for middagsklubben — registrer kvelder, meny, vin, poenggiving og bilder.

## Kom i gang

### 1. Klon repoet

```bash
git clone https://github.com/JonasRunningen/middagsklubben.git
cd middagsklubben
```

### 2. Opprett virtuelt miljø og installer avhengigheter

```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Kjør appen

```bash
flask run
```

Åpne [http://localhost:5000](http://localhost:5000) i nettleseren.

Databasen (`middagsklubben.db`) opprettes automatisk ved første oppstart.

---

## Funksjoner

- **Rotasjon** — Hold orden på hvem som er neste vert, med manuell overstyring
- **Kveldsoversikt** — Dato, vert, meny (forrett/hoved/dessert), kategori og notater
- **Vin & øl** — Logg navn, årgang, druetype og smaksnotater per kveld
- **Poenggiving** — Stjernerating (1–5) og kommentar fra hvert medlem
- **Bilder** — Last opp og vis bilder fra kvelden
- **Statistikk** — Antall middager, snittpoeng, beste kveld og totalt antall flasker

## Filstruktur

```
middagsklubben/
├── app.py              ← Flask-app og routes
├── models.py           ← SQLAlchemy-modeller
├── requirements.txt
├── static/
│   ├── style.css
│   └── uploads/        ← Opplastede bilder (ikke i git)
└── templates/
    ├── base.html
    ├── index.html
    ├── ny_kveld.html
    ├── kveld.html
    ├── statistikk.html
    └── medlemmer.html
```

## Teknisk stack

- Python 3.11+ / Flask
- SQLite via SQLAlchemy
- Jinja2 templates
- Vanilla CSS og JS — ingen rammeverk
