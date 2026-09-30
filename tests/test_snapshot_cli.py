"""Tests bout en bout de la veille snapshots (fetch et gh simulés)."""
import pytest

from agent.snapshot_cli import SnapshotRunner, parse_args
from agent.tools.benefit_loader import load_dispositifs
from agent.tools.snapshot_store import SnapshotStore

LINK = "https://www.exemple.fr/aides/permis"
YAML = f"""label: Aide au permis
institution: departement_exemple
description: Une aide pour financer le permis.
type: float
periodicite: ponctuelle
montant: 500
link: {LINK}
"""
FILLER = ("<p>Cette aide est destinée aux jeunes de 18 à 25 ans résidant dans le "
          "département. Elle finance une partie de la formation au permis B "
          "auprès d'une auto-école partenaire du dispositif départemental.</p>")


def page(*paragraphs):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<html><body><nav>Accueil Menu</nav><main><article>"
            f"<h1>Aide au permis</h1>{FILLER}{body}{FILLER.replace('B', 'AM')}"
            f"</article></main><footer>Mentions légales</footer></body></html>")


OLD = page("Le montant de l'aide est de 500 €.", "Dépôt avant le 30/06/2026.")
NEW = page("Le montant de l'aide est de 600 €.", "Dépôt avant le 30/06/2026.")
CLOSED = page("Le montant de l'aide est de 500 €.", "Dépôt avant le 30/06/2026.",
              "Le dispositif est désormais clôturé.")


class FakeFetch:
    """Sert un HTML par URL ; `pages` peut être remplacé entre deux runs."""

    def __init__(self, html, **overrides):
        self.html = html
        self.overrides = overrides
        self.calls = 0

    async def __call__(self, url):
        self.calls += 1
        html = self.html() if callable(self.html) else self.html
        return {"status": 200, "content_type": "text/html; charset=utf-8",
                "final_url": url, "html": html, **self.overrides}


class FakePR:
    def __init__(self, prs=(), gh_ok=True, fail_create=False):
        self.prs = list(prs)
        self.gh_ok = gh_ok
        self.fail_create = fail_create
        self.created, self.comments = [], []

    def list_prs(self):
        return self.prs if self.gh_ok else None

    def find_open_pr(self, slug):
        return next((p["url"] for p in self.prs if p["slug"] == slug
                     and p["state"] == "OPEN"), None)

    def is_refused(self, slug):
        return any(p["slug"] == slug and p["state"] == "CLOSED" for p in self.prs)

    def create(self, slug, file_rel_path, title, body, draft, today, prefix="update"):
        if self.fail_create:
            raise RuntimeError("push refusé")
        self.created.append({"slug": slug, "title": title, "body": body,
                             "draft": draft, "today": today, "prefix": prefix})
        url = f"https://gh/pr/{len(self.created)}"
        self.prs.append({"slug": slug, "state": "OPEN", "url": url})
        return url

    def comment(self, pr, body):
        self.comments.append((pr, body))


class ExplodingPR:
    def __getattr__(self, name):
        raise AssertionError(f"gh appelé en mode off : {name}")


@pytest.fixture
def env(tmp_path):
    benefits = tmp_path / "benefits"
    benefits.mkdir()
    (benefits / "aide-permis.yml").write_text(YAML, encoding="utf-8")
    store = SnapshotStore(tmp_path / "snaps", keep=8)

    def make(fetch, pr=None, pr_mode="draft", today="20260930", **kw):
        return SnapshotRunner(
            store=store, dispositifs=load_dispositifs(benefits), fetch=fetch,
            pr_service=pr, pr_mode=pr_mode, today=today,
            reports_dir=tmp_path / "reports", **kw)

    return {"make": make, "store": store, "yml": benefits / "aide-permis.yml",
            "tmp": tmp_path}


async def _baseline(env, html=OLD):
    return await env["make"](FakeFetch(html), pr_mode="off", today="20260923").run()


def _only(out):
    assert len(out["results"]) == 1
    return out["results"][0]


async def test_first_run_is_baseline(env):
    res = _only(await _baseline(env))
    assert res["status"] == "baseline"
    assert env["store"].latest_before("aide-permis", "20260930")["date"] == "20260923"


async def test_unchanged_page(env):
    await _baseline(env)
    res = _only(await env["make"](FakeFetch(OLD), pr=ExplodingPR()).run())
    assert res["status"] == "unchanged"


async def test_mode_off_never_calls_gh_and_saves(env):
    await _baseline(env)
    res = _only(await env["make"](FakeFetch(NEW), pr=ExplodingPR(), pr_mode="off").run())
    assert (res["status"], res["action"]) == ("triggered", "pr_off")
    assert res["triggers"] == {"montants": (["500"], ["600"])}
    assert env["store"].latest_before("aide-permis", "20261001")["date"] == "20260930"
    assert env["yml"].read_text(encoding="utf-8") == YAML


async def test_new_pr_with_header_comment_and_diff_comment(env):
    await _baseline(env)
    pr = FakePR()
    res = _only(await env["make"](FakeFetch(NEW), pr=pr).run())
    assert res["action"] == "pr_opened"
    assert res["pr_url"] == "https://gh/pr/1"
    created = pr.created[0]
    assert created["prefix"] == "snapshot"
    assert created["draft"] is True
    assert created["today"] == "20260930"
    assert created["title"] == "veille: Aide au permis — page modifiée (montants)"
    assert "`500` → `600`" in created["body"]
    assert env["yml"].read_text(encoding="utf-8").startswith(
        "# veille-snapshot 2026-09-30 : page modifiée (montants) — à vérifier\n")
    url, comment = pr.comments[0]
    assert url == "https://gh/pr/1"
    assert "Snapshot du 2026-09-30 vs 2026-09-23" in comment
    assert "-Le montant de l'aide est de 500 €." in comment
    assert "+Le montant de l'aide est de 600 €." in comment
    assert env["store"].latest_before("aide-permis", "20261001")["date"] == "20260930"


async def test_strong_cloture_marks_private(env):
    await _baseline(env)
    pr = FakePR()
    res = _only(await env["make"](FakeFetch(CLOSED), pr=pr).run())
    assert res["action"] == "pr_opened"
    assert pr.created[0]["title"] == (
        "veille: Aide au permis — clôture détectée, passage en private")
    assert "private: true" in env["yml"].read_text(encoding="utf-8")


async def test_open_pr_gets_comment_once(env):
    await _baseline(env)
    pr = FakePR(prs=[{"slug": "aide-permis", "state": "OPEN", "url": "https://gh/pr/9"}])
    res = _only(await env["make"](FakeFetch(NEW), pr=pr).run())
    assert res["action"] == "commented"
    assert pr.created == []
    assert [c[0] for c in pr.comments] == ["https://gh/pr/9"]
    assert env["yml"].read_text(encoding="utf-8") == YAML
    # 2e run le même jour : même diff, rien de reposté
    res = _only(await env["make"](FakeFetch(NEW), pr=pr).run())
    assert res["action"] == "already_posted"
    assert len(pr.comments) == 1


async def test_refused_pr_blocks(env):
    await _baseline(env)
    pr = FakePR(prs=[{"slug": "aide-permis", "state": "CLOSED", "url": "u"}])
    res = _only(await env["make"](FakeFetch(NEW), pr=pr).run())
    assert res["action"] == "blocked"
    assert pr.created == [] and pr.comments == []


async def test_gh_unavailable_sends_nothing(env):
    await _baseline(env)
    pr = FakePR(gh_ok=False)
    res = _only(await env["make"](FakeFetch(NEW), pr=pr).run())
    assert res["action"] == "pr_error"
    assert pr.created == [] and pr.comments == []
    assert env["store"].latest_before("aide-permis", "20261001")["date"] == "20260923"


async def test_pr_error_does_not_save_snapshot(env):
    await _baseline(env)
    res = _only(await env["make"](FakeFetch(NEW), pr=FakePR(fail_create=True)).run())
    assert res["action"] == "pr_error"
    assert "push refusé" in res["error"]
    assert env["store"].latest_before("aide-permis", "20261001")["date"] == "20260923"


async def test_dry_run_writes_nothing(env):
    await _baseline(env)
    pr = FakePR()
    out = await env["make"](FakeFetch(NEW), pr=pr).run(dry_run=True)
    res = _only(out)
    assert res["action"] == "would_open_pr"
    assert pr.created == [] and pr.comments == []
    assert env["yml"].read_text(encoding="utf-8") == YAML
    assert env["store"].latest_before("aide-permis", "20261001")["date"] == "20260923"
    assert "dry-run" in out["report_path"].read_text(encoding="utf-8")


async def test_baseline_flag_saves_without_comparing(env):
    await _baseline(env)
    res = _only(await env["make"](FakeFetch(NEW), pr=ExplodingPR()).run(baseline=True))
    assert res["status"] == "baseline"
    assert env["store"].latest_before("aide-permis", "20261001")["date"] == "20260930"


async def test_link_change_starts_new_baseline(env):
    await _baseline(env)
    env["yml"].write_text(YAML.replace(LINK, LINK + "-2026"), encoding="utf-8")
    res = _only(await env["make"](FakeFetch(NEW), pr=ExplodingPR()).run())
    assert res["status"] == "baseline"
    assert res["reasons"] == ["url_changee"]


async def test_trap_page_is_suspect_and_not_saved(env):
    await _baseline(env)
    trap = "<html><body><p>Access denied. Please complete the captcha.</p></body></html>"
    res = _only(await env["make"](FakeFetch(trap), pr=ExplodingPR()).run())
    assert res["status"] == "suspect"
    assert env["store"].latest_before("aide-permis", "20261001")["date"] == "20260923"


async def test_trap_page_is_never_a_baseline(env):
    trap = "<html><body><p>Access denied. Please complete the captcha.</p></body></html>"
    res = _only(await _baseline(env, trap))
    assert res["status"] == "suspect"
    assert env["store"].latest_before("aide-permis", "20261001") is None


async def test_redirect_is_suspect(env):
    await _baseline(env)
    fetch = FakeFetch(NEW, final_url="https://www.exemple.fr/")
    res = _only(await env["make"](fetch, pr=ExplodingPR()).run())
    assert res["status"] == "suspect"
    assert "redirection" in res["reasons"]


async def test_unstable_page_is_suspect(env):
    await _baseline(env)
    versions = iter([NEW, page("Le montant de l'aide est de 700 €.",
                               "Dépôt avant le 30/06/2026.")])
    fetch = FakeFetch(lambda: next(versions))
    res = _only(await env["make"](fetch, pr=ExplodingPR()).run())
    assert res["status"] == "suspect"
    assert res["reasons"] == ["instable"]
    assert fetch.calls == 2


async def test_fetch_error_keeps_previous_snapshot(env):
    await _baseline(env)
    res = _only(await env["make"](FakeFetch(NEW, status=503), pr=ExplodingPR()).run())
    assert res["status"] == "fetch_error"
    assert env["store"].latest_before("aide-permis", "20261001")["date"] == "20260923"


async def test_only_filters_slugs(env):
    out = await env["make"](FakeFetch(OLD), pr_mode="off").run(only=["autre"])
    assert out["results"] == []


async def test_report_is_written(env):
    await _baseline(env)
    out = await env["make"](FakeFetch(NEW), pr=FakePR()).run()
    text = out["report_path"].read_text(encoding="utf-8")
    assert out["report_path"].name.startswith("snapshot-")
    assert "## Dates/montants modifiés (1)" in text
    assert "`aide-permis`" in text


def test_parse_args():
    assert parse_args(["--only", "a", "b", "--dry-run"]) == {
        "only": ["a", "b"], "dry_run": True, "baseline": False}
    with pytest.raises(SystemExit):
        parse_args(["--dry-run", "--baseline"])
