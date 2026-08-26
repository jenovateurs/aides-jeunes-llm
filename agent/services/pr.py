"""Ouverture de PR sur aides-jeunes via git + gh CLI (interface abstraite)."""
import json
import os
import re
import subprocess
from pathlib import Path

# Noms de branche produits par `create` : veille/<update|revive>-<slug>-<YYYYMMDD>.
# Les deux préfixes partagent le même espace de déduplication : une PR de
# réactivation refusée doit bloquer une nouvelle PR sur le même dispositif,
# et inversement.
BRANCH_RE = re.compile(r"^veille/(?:update|revive)-(?P<slug>.+)-(?P<date>\d{8})$")


def _looks_invalid_token(value: str) -> bool:
    """Vrai si la valeur n'est pas un token GitHub plausible.

    Un token GitHub réel (ghp_…, github_pat_…, gho_…) est non vide, sans espace
    ni `#`. Un `.env` peut contenir un placeholder/commentaire (`GITHUB_TOKEN=`
    vide, ou `= # à remplir`) qui, injecté dans os.environ, fait préférer à gh
    ce token bidon → 401. On les considère invalides pour retomber sur le keyring.
    """
    v = value.strip()
    return (not v) or any(c.isspace() for c in v) or v.startswith("#")


def _clean_env() -> dict:
    """Env sans tokens GitHub invalides : gh retombe alors sur le keyring."""
    env = os.environ.copy()
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
        if key in env and _looks_invalid_token(env[key]):
            env.pop(key)
    return env


class PRService:
    """Crée une PR par dispositif. `runner` injectable pour les tests.

    - `remote` : nom du remote git à pousser (souvent un fork, ex 'aides-jeunes-bot').
    - `base_remote` : remote dont part la branche. **Doit pointer le repo cible**
      (ex 'origin' = betagouv) : brancher depuis un fork non synchronisé produit
      des PR qui rejouent des correctifs déjà mergés, voire annulent des
      changements upstream. Vide = `remote`.
    - `base_repo` : repo cible de la PR (ex 'betagouv/aides-jeunes'). Vide = même repo.
    - `head_owner` : propriétaire de la branche head pour une PR cross-fork
      (ex 'aides-jeunes-bot'). Vide = branche locale.
    """

    def __init__(self, repo_root: Path, runner=subprocess.run,
                 remote: str = "origin", base_repo: str = "", head_owner: str = "",
                 base_remote: str = ""):
        self.repo_root = Path(repo_root)
        self.runner = runner
        self.remote = remote
        self.base_remote = base_remote or remote
        self.base_repo = base_repo
        self.head_owner = head_owner
        self._blocking_heads = None  # cache par instance

    def _run(self, cmd: list[str]) -> str:
        res = self.runner(
            cmd, cwd=str(self.repo_root), capture_output=True, text=True,
            check=False, env=_clean_env(),
        )
        if res.returncode != 0:
            raise RuntimeError(f"Commande échouée: {' '.join(cmd)}\n{res.stderr}")
        return (res.stdout or "").strip()

    def _blocking_pr_heads(self):
        """Branches des PR qui interdisent une nouvelle PR pour un dispositif.

        Bloquantes : PR **ouvertes** (doublon) et PR **fermées sans merge**
        (refus humain, à ne pas rejouer). Les PR mergées ne bloquent pas : la
        branche survit sur le fork après merge, s'y fier gelait le dispositif
        pour toujours. `None` si `gh` est indisponible → repli sur git.
        """
        if self._blocking_heads is not None:
            return self._blocking_heads
        cmd = ["gh", "pr", "list", "--state", "all", "--limit", "500",
               "--json", "headRefName,state"]
        if self.base_repo:
            cmd += ["--repo", self.base_repo]
        res = self.runner(cmd, cwd=str(self.repo_root), capture_output=True,
                          text=True, check=False, env=_clean_env())
        if res.returncode != 0:
            return None
        try:
            prs = json.loads(res.stdout or "[]")
        except ValueError:
            return None
        self._blocking_heads = {
            pr["headRefName"] for pr in prs
            if str(pr.get("state", "")).upper() != "MERGED"
        }
        return self._blocking_heads

    def branch_exists(self, slug: str) -> bool:
        """Vrai si une PR ouverte ou refusée existe déjà pour ce dispositif."""
        heads = self._blocking_pr_heads()
        if heads is not None:
            # slug exact : un préfixe matcherait 'a' sur une PR de 'a-bis'.
            return any((m := BRANCH_RE.match(h)) and m.group("slug") == slug
                       for h in heads)
        # Repli sans gh : présence d'une branche distante (ancien comportement).
        res = self.runner(
            ["git", "ls-remote", "--heads", self.remote, f"veille/*-{slug}-*"],
            cwd=str(self.repo_root), capture_output=True, text=True, check=False,
            env=_clean_env(),
        )
        return bool((res.stdout or "").strip())

    def create(self, slug, file_rel_path, title, body, draft, today,
               prefix: str = "update") -> str:
        """Ouvre la PR. `prefix` = 'update' (veille) ou 'revive' (réactivation)."""
        branch = f"veille/{prefix}-{slug}-{today}"
        # Base = repo cible, pas le fork : sinon la PR part d'un main obsolète.
        self._run(["git", "fetch", self.base_remote, "main"])
        self._run(["git", "checkout", "-b", branch, f"{self.base_remote}/main"])
        self._run(["git", "add", file_rel_path])
        self._run(["git", "commit", "-m", title])
        self._run(["git", "push", "-u", self.remote, branch])
        gh_cmd = ["gh", "pr", "create", "--base", "main",
                  "--title", title, "--body", body]
        if self.base_repo:
            gh_cmd += ["--repo", self.base_repo]
        head = f"{self.head_owner}:{branch}" if self.head_owner else branch
        gh_cmd += ["--head", head]
        if draft:
            gh_cmd.append("--draft")
        out = self._run(gh_cmd)
        return out.splitlines()[-1].strip() if out else ""
