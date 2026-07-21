"""Ouverture de PR sur aides-jeunes via git + gh CLI (interface abstraite)."""
import os
import subprocess
from pathlib import Path


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
    - `base_repo` : repo cible de la PR (ex 'betagouv/aides-jeunes'). Vide = même repo.
    - `head_owner` : propriétaire de la branche head pour une PR cross-fork
      (ex 'aides-jeunes-bot'). Vide = branche locale.
    """

    def __init__(self, repo_root: Path, runner=subprocess.run,
                 remote: str = "origin", base_repo: str = "", head_owner: str = ""):
        self.repo_root = Path(repo_root)
        self.runner = runner
        self.remote = remote
        self.base_repo = base_repo
        self.head_owner = head_owner

    def _run(self, cmd: list[str]) -> str:
        res = self.runner(
            cmd, cwd=str(self.repo_root), capture_output=True, text=True,
            check=False, env=_clean_env(),
        )
        if res.returncode != 0:
            raise RuntimeError(f"Commande échouée: {' '.join(cmd)}\n{res.stderr}")
        return (res.stdout or "").strip()

    def branch_exists(self, slug: str) -> bool:
        res = self.runner(
            ["git", "ls-remote", "--heads", self.remote, f"veille/update-{slug}*"],
            cwd=str(self.repo_root), capture_output=True, text=True, check=False,
            env=_clean_env(),
        )
        return bool((res.stdout or "").strip())

    def create(self, slug, file_rel_path, title, body, draft, today) -> str:
        branch = f"veille/update-{slug}-{today}"
        self._run(["git", "fetch", self.remote, "main"])
        self._run(["git", "checkout", "-b", branch, f"{self.remote}/main"])
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
