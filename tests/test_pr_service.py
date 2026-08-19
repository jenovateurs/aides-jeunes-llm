"""Tests PRService : la branche doit partir du repo cible, pas du fork."""
import json
import subprocess

from agent.services.pr import PRService


class _Runner:
    """Faux subprocess.run : mémorise les commandes, réussit toujours."""

    def __init__(self, stdout=""):
        self.calls = []
        self.stdout = stdout

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout, stderr="")

    def cmd(self, *prefix):
        """Première commande commençant par `prefix`."""
        for c in self.calls:
            if list(c[:len(prefix)]) == list(prefix):
                return c
        raise AssertionError(f"commande absente: {prefix} dans {self.calls}")


def _pr(runner, **kw):
    return PRService("/repo", runner=runner, **kw)


def test_branch_starts_from_base_remote_not_fork(tmp_path):
    # remote de push = fork, base = repo cible : la branche part de origin/main
    runner = _Runner(stdout="https://github.com/betagouv/aides-jeunes/pull/1")
    pr = _pr(runner, remote="aides-jeunes-bot", base_remote="origin",
             base_repo="betagouv/aides-jeunes", head_owner="aides-jeunes-bot")
    url = pr.create("a", "data/benefits/javascript/a.yml", "t", "b",
                    draft=True, today="20260819")

    assert runner.cmd("git", "fetch") == ["git", "fetch", "origin", "main"]
    assert runner.cmd("git", "checkout") == [
        "git", "checkout", "-b", "veille/update-a-20260819", "origin/main"]
    # le push va bien sur le fork
    assert runner.cmd("git", "push") == [
        "git", "push", "-u", "aides-jeunes-bot", "veille/update-a-20260819"]
    gh = runner.cmd("gh", "pr", "create")
    assert "--head" in gh and gh[gh.index("--head") + 1] == \
        "aides-jeunes-bot:veille/update-a-20260819"
    assert "--draft" in gh
    assert url == "https://github.com/betagouv/aides-jeunes/pull/1"


def test_base_remote_defaults_to_remote():
    # sans base_remote explicite : comportement historique (mono-repo)
    runner = _Runner()
    pr = _pr(runner, remote="origin")
    assert pr.base_remote == "origin"
    pr.create("a", "f.yml", "t", "b", draft=False, today="20260819")
    assert runner.cmd("git", "checkout")[-1] == "origin/main"
    assert "--draft" not in runner.cmd("gh", "pr", "create")


def test_only_the_target_file_is_committed():
    runner = _Runner()
    _pr(runner, remote="origin").create(
        "a", "data/benefits/openfisca/aah.yml", "t", "b",
        draft=False, today="20260819")
    assert runner.cmd("git", "add") == [
        "git", "add", "data/benefits/openfisca/aah.yml"]


class _PRListRunner(_Runner):
    """Répond à `gh pr list` avec un jeu de PR, échoue sur le reste."""

    def __init__(self, prs):
        super().__init__()
        self.prs = prs

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(self.prs), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_open_pr_blocks_new_pr():
    runner = _PRListRunner([
        {"headRefName": "veille/update-a-20260819", "state": "OPEN"}])
    assert _pr(runner, remote="aides-jeunes-bot",
               base_repo="betagouv/aides-jeunes").branch_exists("a") is True


def test_closed_unmerged_pr_blocks_new_pr():
    # PR refusée par un humain : ne pas la rejouer
    runner = _PRListRunner([
        {"headRefName": "veille/update-a-20260805", "state": "CLOSED"}])
    assert _pr(runner, base_repo="betagouv/aides-jeunes").branch_exists("a") is True


def test_merged_pr_does_not_block():
    # la branche survit sur le fork après merge : s'y fier gelait le dispositif
    runner = _PRListRunner([
        {"headRefName": "veille/update-a-20260715", "state": "MERGED"}])
    pr = _pr(runner, base_repo="betagouv/aides-jeunes")
    assert pr.branch_exists("a") is False
    # et aucun repli sur git ls-remote
    assert not any(c[:2] == ["git", "ls-remote"] for c in runner.calls)


def test_slug_prefix_is_not_matched_loosely():
    # 'a' ne doit pas être bloqué par une PR sur 'a-bis'
    runner = _PRListRunner([
        {"headRefName": "veille/update-a-bis-20260819", "state": "OPEN"}])
    pr = _pr(runner, base_repo="betagouv/aides-jeunes")
    assert pr.branch_exists("a-bis") is True
    assert pr.branch_exists("a") is False


def test_pr_list_result_is_cached():
    runner = _PRListRunner([])
    pr = _pr(runner, base_repo="betagouv/aides-jeunes")
    pr.branch_exists("a")
    pr.branch_exists("b")
    assert sum(1 for c in runner.calls if c[:3] == ["gh", "pr", "list"]) == 1


def test_fallback_to_git_when_gh_unavailable():
    class _NoGh(_Runner):
        def __call__(self, cmd, **kwargs):
            self.calls.append(cmd)
            if cmd[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no gh")
            return subprocess.CompletedProcess(
                cmd, 0, stdout="deadbeef\trefs/heads/veille/update-a-20260715",
                stderr="")

    runner = _NoGh()
    pr = _pr(runner, remote="aides-jeunes-bot", base_repo="betagouv/aides-jeunes")
    assert pr.branch_exists("a") is True
    assert runner.cmd("git", "ls-remote") == [
        "git", "ls-remote", "--heads", "aides-jeunes-bot", "veille/update-a*"]


def test_failed_command_raises():
    class _Fail(_Runner):
        def __call__(self, cmd, **kwargs):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    try:
        _pr(_Fail(), remote="origin").create(
            "a", "f.yml", "t", "b", draft=False, today="20260819")
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("RuntimeError attendue")
