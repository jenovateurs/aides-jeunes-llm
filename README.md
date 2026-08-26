# aj-llm — Agents IA pour Aides Jeunes

Outils LLM pour alimenter et maintenir la base Aides Jeunes. **Ce projet vit dans son propre dépôt Git** ([`jenovateurs/aides-jeunes-llm`](https://github.com/jenovateurs/aides-jeunes-llm)) et se **clone dans le dossier `tools/` du repo `aides-jeunes`** (→ `tools/aj-llm/`) pour que les chemins relatifs vers `data/` fonctionnent.

- **Agent 1 — Contribution** : formate un YAML brut en fiche `benefits` valide, et l'écrit **directement** dans `data/benefits/javascript/`.
- **Agent 2 — URL** : scrape une page et génère la fiche _(à venir)_
- **Agent 3 — Veille** : détecte liens cassés / contenus obsolètes et ouvre des PR sur `aides-jeunes` (CLI + API).

> **Écriture directe, sans PR.** En mode local (`PUBLISH_MODE=dev`, défaut), l'outil modifie les fichiers du repo sur disque. Tu relis le diff et committes toi-même. Aucune gestion de PR GitHub.

---

## Emplacement

Ce dépôt doit être **cloné dans le dossier `tools/` du repo [`aides-jeunes`](https://github.com/betagouv/aides-jeunes)**, sous le nom `aj-llm` :

```bash
# 1. Cloner le repo principal (si pas déjà fait)
git clone git@github.com:betagouv/aides-jeunes.git
# 2. Cloner cet outil dans tools/aj-llm
cd aides-jeunes/tools
git clone git@github.com:jenovateurs/aides-jeunes-llm.git aj-llm
```

Résultat attendu :

```
aides-jeunes/
├── data/                 ← cible des écritures (benefits, institutions…)
└── tools/
    └── aj-llm/           ← ce dépôt cloné ici
```

L'outil écrit dans `../../data` par rapport à `tools/aj-llm/` (résolu automatiquement). Surcharge possible via `AIDES_JEUNES_DATA_PATH`.

> ⚠️ Le nom du dossier **doit être `aj-llm`** et se trouver dans `tools/` : les chemins vers `data/` sont relatifs à cet emplacement.

---

## Prérequis

- Python 3.11+ avec [`uv`](https://docs.astral.sh/uv/)
- Node.js 18+ avec `pnpm`
- `gh` (GitHub CLI) authentifié — **uniquement** pour l'Agent 3 en mode PR
- Redis : variables `REDIS_*` présentes dans la config mais **aucun code ne s'y connecte** aujourd'hui — rien à installer

---

## Installation

```bash
cd tools/aj-llm

# Backend
uv sync

# Frontend
cd frontend && pnpm install && cd ..

# Config
cp .env.example .env   # puis renseigner OPENAI_API_KEY / MODEL_NAME
```

---

## Lancer (local)

### Backend seul (API + frontend buildé)

```bash
cd tools/aj-llm
uv run uvicorn backend.main:app --reload --port 8000
```

API sur `http://localhost:8000`.

### Dev frontend (hot-reload)

```bash
# Terminal 1 — backend
uv run uvicorn backend.main:app --reload --port 8000
# Terminal 2 — frontend
cd frontend && pnpm dev
```

UI sur `http://localhost:5173` (proxy `/api` → `:8000`).

---

## Workflow Agent 1 (contribution → écriture directe)

1. **Formater** un YAML brut :

   ```bash
   curl -s -X POST http://localhost:8000/api/contribution/format \
     -H "Content-Type: application/json" \
     -d '{"yaml_content":"label: aide au BAFA\ninstitution: caf_haute_savoie\ndescription: ...\ntype: float\nperiodicite: ponctuelle\nunit: \"€\"\nmontant: 109\nlink: https://www.caf.fr\n"}' \
     | python3 -m json.tool
   ```

   Réponse : `yaml_content` + `filename` (ex. `aide-au-bafa-caf-haute-savoie.yml`).

2. **Écrire** la fiche dans `data/benefits/javascript/` :

   ```bash
   curl -s -X POST http://localhost:8000/api/contribution/save \
     -H "Content-Type: application/json" \
     -d '{"yaml_content":"...","filename":"aide-au-bafa-caf-haute-savoie.yml"}'
   ```

   Réponse : `{"status":"success","path":".../data/benefits/javascript/aide-au-bafa-caf-haute-savoie.yml"}`.

3. **Relire + committer** dans `aides-jeunes` :
   ```bash
   cd ../..        # racine aides-jeunes
   git diff data/benefits/javascript/
   git add data/benefits/javascript/<fichier>.yml && git commit
   ```

> `filename` est strictement validé (`slug-kebab-case.yml`, sans chemin) — anti path-traversal.

---

## Agent 3 — Veille (CLI)

Dossiers scannés : `data/benefits/javascript/` **et** `data/benefits/openfisca/`
(490 fiches publiques, 803 liens), configurable via `VEILLE_BENEFITS_DIRS`. Le chemin
de la PR suit le dossier d'origine de la fiche.

Pipeline : `load_pending` → `check_links` → `check_content` → `apply_updates_and_pr` → rapport.
Chaque run écrit un rapport Markdown dans `reports/veille-<timestamp>.md` et met à jour l'état
dans `.veille/state.json` (évite de re-vérifier les mêmes fiches avant `VEILLE_RECHECK_DAYS`).

```bash
cd tools/aj-llm
uv run python -m agent.veille_cli \
  [--limit N] [--only SLUG ...] [--model-name NAME] [--links-only] [--covoiturage]
```

| Option | Défaut | Effet |
|---|---|---|
| `--limit N` | `10` | Nombre de fiches traitées. **Plafonné par `VEILLE_DAILY_BATCH`** (`min(limit, batch)`) : pour dépasser 10, augmenter aussi la variable. |
| `--only SLUG ...` | _(vide)_ | Restreint le run à ces slugs. Court-circuite la priorisation stats, l'état et `--limit`. |
| `--model-name NAME` | _(config)_ | Force le modèle LLM (sinon `MODEL_NAME` du `.env`). |
| `--links-only` | `false` | **Liens cassés uniquement** : saute l'analyse de contenu (montant / conditions), aucun appel LLM, aucune page téléchargée. Run rapide. |
| `--covoiturage` | `false` | Ajoute les 111 incitations covoiturage (`dynamic/incitations-covoiturage.json`). Vérification des liens **seule** : ce sont des entrées JSON sans fiche YAML, donc jamais de PR. |

### Modes de PR

`VEILLE_PR_MODE` pilote l'écriture : `off` (défaut, rapport seul), `draft` (PR brouillon), `ready` (PR ouverte).
Précédence : un lien cassé donne une PR `private: true`; sinon un contenu divergent avec
confiance ≥ `VEILLE_CONFIDENCE_MIN` donne une PR de mise à jour `montant` / `conditions`.
En `--links-only`, seules les PR `private: true` sont possibles.

### Exemples

```bash
# 1. Repérage sans rien pousser (rapport seul)
uv run python -m agent.veille_cli --limit 20

# 2. Une seule fiche, en debug
uv run python -m agent.veille_cli --only aide-au-bafa-caf-haute-savoie

# 3. Chasse aux liens cassés sur tout le stock, PR brouillon (cap 20 PR)
VEILLE_PR_MODE=draft VEILLE_DAILY_BATCH=150 \
VEILLE_GIT_REMOTE=aides-jeunes-bot VEILLE_PR_REPO=betagouv/aides-jeunes VEILLE_PR_HEAD=aides-jeunes-bot \
uv run python -m agent.veille_cli --limit 150 --links-only

# 4. Liens openfisca uniquement (aides nationales, service-public.fr)
VEILLE_PR_MODE=off VEILLE_BENEFITS_DIRS=openfisca VEILLE_DAILY_BATCH=120 \
uv run python -m agent.veille_cli --limit 120 --links-only

# 5. Veille complète (liens + contenu, appels LLM)
VEILLE_PR_MODE=draft VEILLE_DAILY_BATCH=50 \
uv run python -m agent.veille_cli --limit 50
```

### Classes de liens

- `ok` : HTTP 200.
- `broken` : 404, 410, 5xx, DNS mort / connexion refusée → **PR `private: true`**.
- `suspicious` : 401, 403, 429, erreur réseau transitoire (anti-bot probable) → vérif humaine, **pas de PR**.
- `ignored` : domaine ou URL listé dans `veille-link-ignore.yml`.

Un faux positif se corrige en ajoutant le domaine à `veille-link-ignore.yml` plutôt qu'en acceptant la PR.

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `VEILLE_PR_MODE` | `off` | `off` \| `draft` \| `ready` |
| `VEILLE_MAX_PR` | `20` | Cap dur de PR envoyées par run |
| `VEILLE_DAILY_BATCH` | `10` | Plafond du `--limit` |
| `VEILLE_RECHECK_DAYS` | `30` | Délai avant re-vérification d'une fiche |
| `VEILLE_CONCURRENCY` | `3` | Requêtes HTTP en parallèle |
| `VEILLE_CONFIDENCE_MIN` | `0.8` | Confiance LLM minimale pour une PR de contenu |
| `VEILLE_GIT_REMOTE` | `origin` | Remote (fork) où pousser la branche |
| `VEILLE_PR_BASE_REMOTE` | `origin` | Remote dont **part** la branche. Doit viser le repo cible : brancher depuis un fork en retard produit des PR qui annulent des correctifs déjà mergés. |
| `VEILLE_BENEFITS_DIRS` | `javascript,openfisca` | Dossiers de `data/benefits/` scannés |
| `VEILLE_PR_REPO` | _(vide)_ | Repo cible de la PR, ex. `betagouv/aides-jeunes` |
| `VEILLE_PR_HEAD` | _(vide)_ | Owner de la branche head (PR cross-fork via `gh`) |
| `VEILLE_STATE_PATH` | `.veille/state.json` | Fichier d'état |
| `VEILLE_REPORTS_DIR` | `reports/` | Dossier des rapports |
| `VEILLE_LINK_IGNORE_PATH` | `veille-link-ignore.yml` | Liste des faux positifs |
| `VEILLE_USER_AGENT` | _(UA navigateur interne)_ | Surcharge du User-Agent |
| `VEILLE_HTTP_TIMEOUT_CONNECT` / `_READ` | `10` / `30` | Timeouts HTTP (s) |
| `VEILLE_HTTP_RETRIES` | `3` | Retries sur erreurs transitoires |
| `VEILLE_HTTP_HOST_DELAY` | `1.5` | Délai mini entre 2 requêtes sur un même hôte (s) |
| `VEILLE_STATS_URL` / `VEILLE_MATOMO_URL` | _(prod)_ | Sources de priorisation des fiches |

> Les PR nécessitent `gh` authentifié et un remote poussable. Sans ça, rester en `VEILLE_PR_MODE=off`.

### Mode revival — réactiver les fiches `private`

Sens inverse de la veille : parcourt les fiches `private: true`, reteste leurs
liens, et pour celles dont **tous** les liens répondent ouvre une PR qui retire
`private` (et met à jour le **montant maximum** si la page en annonce un autre).

#### Par où commencer

**Étape 1 — repérage, aucune écriture.** State envoyé dans `/tmp` pour ne pas
consommer la rotation (sinon les fiches vues sont ignorées pendant
`VEILLE_RECHECK_DAYS`) :

```bash
cd tools/aj-llm
VEILLE_PR_MODE=off \
VEILLE_REVIVAL_STATE_PATH=/tmp/revival-dryrun.json \
VEILLE_REVIVAL_BATCH=70 \
  uv run python -m agent.revival_cli --all-private --links-only --limit 70
```

Aucune PR, aucun appel LLM, aucun `.yml` touché, aucune commande `git`. Lire
ensuite `reports/revival-<timestamp>.md` : les fiches listées `revive_no_pr`
sont celles dont tous les liens répondent.

**Étape 2 — PR brouillon.** Avant de lancer : le repo `aides-jeunes` doit avoir
un arbre de travail propre (`git status`), sinon les fichiers non commités
suivent la branche créée. Les branches partent de `origin/main`, la branche
courante n'a pas d'importance.

```bash
VEILLE_PR_MODE=draft \
VEILLE_GIT_REMOTE=aides-jeunes-bot \
VEILLE_PR_REPO=betagouv/aides-jeunes \
VEILLE_PR_HEAD=aides-jeunes-bot \
VEILLE_MAX_PR=5 \
  uv run python -m agent.revival_cli --all-private --limit 70
```

`VEILLE_MAX_PR=5` limite le premier vrai run à 5 PR, le temps de juger la
qualité des propositions. Sans `--links-only`, le montant maximum est vérifié
par LLM (1 appel par fiche réactivable).

```bash
uv run python -m agent.revival_cli \
  [--limit N] [--only SLUG ...] [--model-name NAME] [--all-private] [--links-only]
```

| Option | Défaut | Effet |
|---|---|---|
| `--limit N` | `10` | Nombre de fiches traitées. Plafonné par `VEILLE_REVIVAL_BATCH` (`30`), **pas** par `VEILLE_DAILY_BATCH`. |
| `--only SLUG ...` | _(vide)_ | Restreint le run à ces slugs. |
| `--model-name NAME` | _(config)_ | Force le modèle LLM. |
| `--all-private` | `false` | Teste **toutes** les fiches `private` (67) au lieu des seules que la veille a elle-même passées en private. Voir l'avertissement ci-dessous. |
| `--links-only` | `false` | Liens seuls : aucun contrôle du montant, aucun appel LLM. |

**Périmètre.** Par défaut, seules les fiches que la veille a elle-même mises en
cause sont retestées — celles dont `.veille/state.json` porte un `pr_url` ou un
`link_status: broken` (≈ 20 fiches). C'est la seule trace machine distinguant
« private car lien mort » de « private par décision métier » (dispositif
terminé, jamais lancé, saisonnier). `--all-private` élargit aux 67 en assumant
ce bruit : **un lien vivant ne garantit pas un dispositif vivant** (page
d'accueil rétablie, campagne closée). Chaque PR porte cet avertissement.

**Règle de réactivation.** Tous les liens de la fiche (`link`, `instructions`,
`form`, `teleservice`) doivent être `ok` ou `ignored`. Un seul `broken` ou
`suspicious` suffit à laisser la fiche en private (`still_broken` au rapport) —
et fait sauter l'appel LLM pour cette fiche.

**Montant.** Seul `montant` est vérifié, via `agent/prompts/revival.yaml`
(`conditions` est hors périmètre, trop peu fiable). La valeur n'est patchée que
si la confiance LLM atteint `VEILLE_CONFIDENCE_MIN` ; sinon la divergence est
seulement signalée dans le corps de la PR.

**Garde-fous.** Branches `veille/revive-<slug>-<date>`, dédupliquées avec les
branches `veille/update-*` : une PR de réactivation fermée sans merge (« cette
aide n'existe plus ») bloque définitivement toute nouvelle proposition sur ce
dispositif. State séparé (`.veille/revival-state.json`) pour ne pas perturber la
rotation de la veille. Rapport dans `reports/revival-<timestamp>.md`.

| Variable | Défaut | Rôle |
|---|---|---|
| `VEILLE_REVIVAL_BATCH` | `30` | Plafond du `--limit` en mode revival |
| `VEILLE_REVIVAL_STATE_PATH` | `.veille/revival-state.json` | State du mode revival |
| `VEILLE_REVIVAL_EXCLUDE` | `benefit_front_test` | Slugs à ne jamais réactiver (fixtures de test du front) |

#### Exemples

```bash
# 1. Repérage sans rien pousser, sans appel LLM
VEILLE_PR_MODE=off uv run python -m agent.revival_cli --links-only --limit 20

# 2. Tout le stock private, rapport seul
VEILLE_PR_MODE=off VEILLE_REVIVAL_BATCH=70 \
  uv run python -m agent.revival_cli --all-private --links-only --limit 70

# 3. Réactivation en PR brouillon, périmètre tracé, montant vérifié
VEILLE_PR_MODE=draft \
  VEILLE_GIT_REMOTE=aides-jeunes-bot VEILLE_PR_REPO=betagouv/aides-jeunes \
  VEILLE_PR_HEAD=aides-jeunes-bot \
  uv run python -m agent.revival_cli --limit 20
```


### Via l'API (SSE)

```bash
curl -N -X POST http://localhost:8000/api/veille/run \
  -H "Content-Type: application/json" \
  -d '{"limit":5,"only":[],"model_name":null,"links_only":true}'
```

Flux d'événements : `progress` (par étape et par fiche), `pr` (une par PR/proposition), `done` (rapport + résumé), `error`.

---

## Tests

```bash
uv run pytest tests/ -v
```

---

## Roadmap déploiement

- **Itération 1 (actuelle)** : outil local lancé à la main (ce README).
- **Itération 2** : montage automatique via **Ansible** (service + Redis provisionnés avec l'infra aides-jeunes).
