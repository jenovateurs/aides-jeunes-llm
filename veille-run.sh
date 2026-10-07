#!/usr/bin/env bash
# Enchaîne les trois veilles, PR en brouillon. Toutes les fiches sont
# vérifiées ; seul le nombre de PR est plafonné (10 par étape) :
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
export VEILLE_PR_REPO=DNUM-SocialGouv/aides-jeunes
export VEILLE_PR_HEAD=aides-jeunes-bot
export VEILLE_MAX_PR=10
# Pas de plafond de fiches : --limit ALL, bornes de lot relevées d'autant.
ALL=100000
export VEILLE_DAILY_BATCH=$ALL
export VEILLE_REVIVAL_BATCH=$ALL
# Pas de rotation : -1 ignore la date de dernier passage, toute fiche est
# revérifiée (le state est gardé : le revival s'appuie sur son historique).
export VEILLE_RECHECK_DAYS="${VEILLE_RECHECK_DAYS:--1}"

# Chaque PR crée une branche et y commite la fiche : des fichiers suivis
# modifiés partiraient avec elle.
if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
  echo "✗ $REPO a des modifications non commitées, abandon." >&2
  exit 1
fi
START_BRANCH="$(git -C "$REPO" branch --show-current)"

# Passphrases demandées maintenant, pas au milieu du run : les PR poussent en
# SSH et les commits sont signés GPG (commit.gpgsign dans ~/.gitconfig).
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_rsa}"
# `ssh -T` sort toujours en 1 (GitHub n'offre pas de shell) : on lit le
# message, pas le code retour — sinon pipefail fait échouer le test.
github_ssh_ok() {
  local out
  out="$(ssh -T git@github.com 2>&1 || true)"
  [[ "$out" == *"successfully authenticated"* ]]
}
if ! github_ssh_ok; then
  echo "→ Clé SSH ($SSH_KEY)"
  ssh-add --apple-use-keychain "$SSH_KEY"
  if ! github_ssh_ok; then
    echo "✗ GitHub refuse la clé SSH, abandon." >&2
    exit 1
  fi
fi
if [ "$(git -C "$REPO" config --get commit.gpgsign)" = "true" ]; then
  echo "→ Clé GPG"
  export GPG_TTY="$(tty)"
  if ! echo veille | gpg --clearsign \
      --local-user "$(git -C "$REPO" config --get user.signingkey)" >/dev/null; then
    echo "✗ Signature GPG impossible, abandon." >&2
    exit 1
  fi
fi

# Chaque étape part de la branche de départ : sinon la suivante lirait les
# fiches depuis la branche de PR laissée par la précédente.
back_to_start() {
  git -C "$REPO" checkout --quiet "$START_BRANCH"
}
trap back_to_start EXIT

echo "→ 1/3 Liens cassés"
uv run python -m agent.veille_cli --links-only --limit $ALL || echo "✗ étape 1 en échec"
back_to_start

echo "→ 2/3 Revival"
uv run python -m agent.revival_cli --links-only --limit $ALL || echo "✗ étape 2 en échec"
back_to_start

echo "→ 3/3 Snapshots"
uv run python -m agent.snapshot_cli || echo "✗ étape 3 en échec"

echo "→ Rapports : $(pwd)/reports"
