#!/usr/bin/env bash
# Enchaîne les trois veilles, PR en brouillon, 10 au plus chacune :
#   1. liens cassés (404, 500…) → fiches passées en private
#   2. revival : fiches private dont les liens revivent
#   3. snapshots : dates / montants / clôture modifiés sur la page `link`
# Aucun appel LLM (--links-only pour 1 et 2, snapshots sans LLM par design).
#
# Usage: ./veille-run.sh

set -euo pipefail
cd "$(dirname "$0")"

REPO="$(cd ../.. && pwd)"

export VEILLE_PR_MODE=draft
export VEILLE_GIT_REMOTE=aides-jeunes-bot
export VEILLE_PR_REPO=betagouv/aides-jeunes
export VEILLE_PR_HEAD=aides-jeunes-bot
export VEILLE_MAX_PR=10

# Chaque PR crée une branche et y commite la fiche : des fichiers suivis
# modifiés partiraient avec elle.
if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
  echo "✗ $REPO a des modifications non commitées, abandon." >&2
  exit 1
fi
START_BRANCH="$(git -C "$REPO" branch --show-current)"

# Chaque étape part de la branche de départ : sinon la suivante lirait les
# fiches depuis la branche de PR laissée par la précédente.
back_to_start() {
  git -C "$REPO" checkout --quiet "$START_BRANCH"
}
trap back_to_start EXIT

echo "→ 1/3 Liens cassés"
uv run python -m agent.veille_cli --links-only --limit 10 || echo "✗ étape 1 en échec"
back_to_start

echo "→ 2/3 Revival"
uv run python -m agent.revival_cli --links-only --limit 10 || echo "✗ étape 2 en échec"
back_to_start

echo "→ 3/3 Snapshots"
uv run python -m agent.snapshot_cli || echo "✗ étape 3 en échec"

echo "→ Rapports : $(pwd)/reports"
