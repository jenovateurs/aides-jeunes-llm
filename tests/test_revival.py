"""Tests du mode revival : fiches `private` dont les liens revivent.

Sens inverse de la veille : on relit les fiches `private: true`, on reteste
leurs liens, et si tous répondent on ouvre une PR qui retire `private` (et
met à jour `montant` si la page annonce un maximum différent).
"""
import asyncio
import json
from pathlib import Path

from agent.agents.veille import VeilleAgent
from agent.revival_cli import parse_args
from agent.tools.benefit_loader import load_dispositifs
from agent.tools.content_check import filter_divergences
from agent.tools.yaml_updater import unmark_private


# ── tools ────────────────────────────────────────────────────────────────

def test_load_dispositifs_only_private(tmp_path):
    (tmp_path / "pub.yml").write_text("label: P\nlink: https://a.fr\n", encoding="utf-8")
    (tmp_path / "priv.yml").write_text(
        "label: X\nprivate: true\nlink: https://b.fr\n", encoding="utf-8")
    out = load_dispositifs(tmp_path, only_private=True)
    assert [d["slug"] for d in out] == ["priv"]


def test_unmark_private_removes_key_and_keeps_comments(tmp_path):
    f = tmp_path / "b.yml"
    f.write_text("label: A  # garde-moi\nprivate: true\nlink: https://a.fr\n",
                 encoding="utf-8")
    diff = unmark_private(f)
    text = f.read_text(encoding="utf-8")
    assert "private" not in text
    assert "# garde-moi" in text
    assert diff == {"before": {"private": True}, "after": {"private": None}}


def test_filter_divergences_keeps_only_allowed_fields():
    # mode revival : `montant` seul est fiable, `conditions` est hors périmètre
    result = filter_divergences({
        "stale": True,
        "divergences": [
            {"champ": "montant", "extrait_source": "120 €"},
            {"champ": "conditions", "extrait_source": "moins de 26 ans"},
        ],
        "proposed": {"montant": 120, "conditions": ["moins de 26 ans"]},
    }, allowed=("montant",))
    assert result["proposed"] == {"montant": 120}
    assert [d["champ"] for d in result["divergences"]] == ["montant"]


# ── pipeline ─────────────────────────────────────────────────────────────

class _FakeLLM:
    """Propose un montant ET des conditions : seul le montant doit passer."""

    def __init__(self, confidence=0.95):
        self.confidence = confidence

    async def generate_json(self, messages):
        return {"stale": True, "confidence": self.confidence,
                "divergences": [{"champ": "montant", "valeur_fiche": 109,
                                 "valeur_page": 120, "extrait_source": "120 €"},
                                {"champ": "conditions", "valeur_fiche": None,
                                 "valeur_page": "x", "extrait_source": "x"}],
                "proposed": {"montant": 120, "conditions": ["x"]}}


class _FakePR:
    def __init__(self):
        self.created = []
    def branch_exists(self, slug):
        return False
    def create(self, slug, file_rel_path, title, body, draft, today,
               prefix="update"):
        self.created.append({"slug": slug, "draft": draft, "prefix": prefix,
                             "title": title, "body": body})
        return f"http://pr/{slug}"


def _write_private(bd, slug, url, montant=109):
    (bd / f"{slug}.yml").write_text(
        f"label: {slug.upper()}\ninstitution: i\nmontant: {montant}\n"
        f"description: d\ntype: float\nperiodicite: ponctuelle\n"
        f"link: {url}\nprivate: true\n", encoding="utf-8")


def _revival_agent(tmp_path, statuses, pr=None, veille_state=None,
                   llm=None, pr_mode="draft"):
    """Agent en mode revival sur une fiche private par entrée de `statuses`."""
    bd = tmp_path / "benefits"
    bd.mkdir(parents=True, exist_ok=True)
    disp = []
    url_status = {}
    for slug, links in statuses.items():
        first_url = list(links)[0]
        _write_private(bd, slug, first_url)
        disp.append({
            "slug": slug, "label": slug.upper(), "institution": "i",
            "path": bd / f"{slug}.yml", "dir": "javascript",
            "links": [{"url": u, "type": "link"} for u in links],
            "yaml": {"montant": 109, "description": "d", "link": first_url,
                     "private": True},
        })
        url_status.update(links)

    async def fake_links(dsp, client, sem):
        return {"slug": dsp["slug"],
                "links": [{"url": l["url"], "type": l["type"],
                           "status": url_status[l["url"]],
                           "ok": url_status[l["url"]] == 200}
                          for l in dsp["links"]]}

    async def fake_page(url, client):
        return "montant maximum 120 € page"

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(
        {"version": 1, "benefits": veille_state or {}}), encoding="utf-8")

    return VeilleAgent(
        load_dispositifs=lambda d, only_private=False: disp if only_private else [],
        fetch_priority=lambda: {s: 1.0 for s in statuses},
        check_links=fake_links, fetch_page=fake_page,
        make_llm=lambda name: llm or _FakeLLM(),
        pr_service=pr, benefits_dir=bd,
        state_path=state_path,
        revival_state_path=tmp_path / "revival-state.json",
        reports_dir=tmp_path / "reports",
        pr_mode=pr_mode, confidence_min=0.8,
        today="2026-07-15", timestamp="20260715-1200",
    )


def _run(agent, **params):
    base = {"limit": 10, "only": [], "model_name": None,
            "revival": True, "all_private": True}
    base.update(params)
    return asyncio.run(agent.run(base))


def test_revival_opens_pr_and_removes_private(tmp_path):
    pr = _FakePR()
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 200}}, pr=pr)
    state = _run(agent)

    assert state["summary"]["prs_opened"] == 1
    assert pr.created[0]["prefix"] == "revive"
    assert pr.created[0]["draft"] is True
    yml = (tmp_path / "benefits" / "a.yml").read_text(encoding="utf-8")
    assert "private" not in yml


def test_revival_patches_montant_maximum(tmp_path):
    pr = _FakePR()
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 200}}, pr=pr)
    _run(agent)

    yml = (tmp_path / "benefits" / "a.yml").read_text(encoding="utf-8")
    assert "montant: 120" in yml
    assert "conditions" not in yml          # hors périmètre revival
    assert "109 → 120" in pr.created[0]["body"] or "120" in pr.created[0]["body"]


def test_revival_keeps_montant_when_confidence_too_low(tmp_path):
    pr = _FakePR()
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 200}}, pr=pr,
                           llm=_FakeLLM(confidence=0.3))
    _run(agent)

    yml = (tmp_path / "benefits" / "a.yml").read_text(encoding="utf-8")
    assert "montant: 109" in yml            # pas patché
    assert "private" not in yml             # mais réactivé quand même
    assert pr.created                       # PR ouverte


def test_revival_no_pr_when_one_link_still_broken(tmp_path):
    pr = _FakePR()
    agent = _revival_agent(
        tmp_path, {"a": {"https://a.fr": 200, "https://dead.fr": 404}}, pr=pr)
    state = _run(agent)

    assert pr.created == []
    assert any(p["action"] == "still_broken" for p in state["pr_results"])
    yml = (tmp_path / "benefits" / "a.yml").read_text(encoding="utf-8")
    assert "private: true" in yml


def test_revival_no_pr_when_link_suspicious(tmp_path):
    # 403 non ignoré : incertain, on ne réactive pas sur un doute
    pr = _FakePR()
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 403}}, pr=pr)
    state = _run(agent)

    assert pr.created == []
    assert any(p["action"] == "still_broken" for p in state["pr_results"])


def test_revival_default_scope_is_state_traced_only(tmp_path):
    # 'a' est tracée cassée par la veille, 'b' est private par décision humaine
    pr = _FakePR()
    agent = _revival_agent(
        tmp_path, {"a": {"https://a.fr": 200}, "b": {"https://b.fr": 200}},
        pr=pr, veille_state={"a": {"link_status": "broken"}})
    state = _run(agent, all_private=False)

    assert [p["slug"] for p in pr.created] == ["a"]
    assert state["summary"]["checked"] == 1


def test_revival_all_private_widens_scope(tmp_path):
    pr = _FakePR()
    agent = _revival_agent(
        tmp_path, {"a": {"https://a.fr": 200}, "b": {"https://b.fr": 200}},
        pr=pr, veille_state={"a": {"link_status": "broken"}})
    state = _run(agent, all_private=True)

    assert sorted(p["slug"] for p in pr.created) == ["a", "b"]
    assert state["summary"]["checked"] == 2


def test_revival_scope_includes_slugs_with_veille_pr(tmp_path):
    pr = _FakePR()
    agent = _revival_agent(
        tmp_path, {"a": {"https://a.fr": 200}, "b": {"https://b.fr": 200}},
        pr=pr, veille_state={"b": {"pr_url": "http://pr/old"}})
    _run(agent, all_private=False)

    assert [p["slug"] for p in pr.created] == ["b"]


def test_revival_writes_its_own_state_file(tmp_path):
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 200}},
                           pr=_FakePR())
    _run(agent)

    revival = json.loads((tmp_path / "revival-state.json").read_text(encoding="utf-8"))
    veille = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert revival["benefits"]["a"]["last_run"] == "2026-07-15"
    assert "a" not in veille["benefits"]     # rotation veille non polluée


def test_revival_pr_mode_off_opens_nothing(tmp_path):
    pr = _FakePR()
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 200}}, pr=pr,
                           pr_mode="off")
    state = _run(agent)

    assert pr.created == []
    assert any(p["action"] == "revive_no_pr" for p in state["pr_results"])
    assert "private: true" in (tmp_path / "benefits" / "a.yml").read_text(encoding="utf-8")


def test_revival_links_only_skips_llm(tmp_path):
    def _boom(name):
        raise AssertionError("aucun LLM ne doit être instancié en links_only")

    pr = _FakePR()
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 200}}, pr=pr)
    agent._make_llm = _boom
    _run(agent, links_only=True)

    yml = (tmp_path / "benefits" / "a.yml").read_text(encoding="utf-8")
    assert "private" not in yml              # réactivation faite
    assert "montant: 109" in yml             # montant intact


def test_revival_report_is_written(tmp_path):
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 200}}, pr=_FakePR())
    state = _run(agent)
    report = Path(state["report_path"])
    assert report.exists()
    assert "réactivation" in report.read_text(encoding="utf-8").lower()


# ── CLI ──────────────────────────────────────────────────────────────────

def test_cli_defaults_to_revival_and_traced_scope():
    params = parse_args([])
    assert params["revival"] is True
    assert params["all_private"] is False


def test_cli_all_private_and_links_only():
    params = parse_args(["--all-private", "--links-only", "--limit", "67",
                         "--only", "a", "b"])
    assert params["all_private"] is True
    assert params["links_only"] is True
    assert params["limit"] == 67
    assert params["only"] == ["a", "b"]


# ── garde-fous ───────────────────────────────────────────────────────────

def test_revival_never_revives_the_test_fixture(tmp_path):
    # `benefit_front_test` est une fiche de test du front, private exprès :
    # la publier injecterait une fausse aide en production.
    pr = _FakePR()
    agent = _revival_agent(
        tmp_path,
        {"benefit_front_test": {"https://a.fr": 200}, "b": {"https://b.fr": 200}},
        pr=pr)
    state = _run(agent, all_private=True)

    assert [p["slug"] for p in pr.created] == ["b"]
    assert state["summary"]["checked"] == 1
    assert "private: true" in (
        tmp_path / "benefits" / "benefit_front_test.yml").read_text(encoding="utf-8")


def test_revival_exclude_list_is_configurable(tmp_path):
    pr = _FakePR()
    agent = _revival_agent(
        tmp_path, {"a": {"https://a.fr": 200}, "b": {"https://b.fr": 200}}, pr=pr)
    agent.revival_exclude = frozenset({"a"})
    _run(agent, all_private=True)

    assert [p["slug"] for p in pr.created] == ["b"]


def test_revival_report_filename_is_distinct(tmp_path):
    # veille et revival écrivent dans le même dossier : préfixes distincts
    agent = _revival_agent(tmp_path, {"a": {"https://a.fr": 200}}, pr=_FakePR())
    state = _run(agent)
    assert Path(state["report_path"]).name == "revival-20260715-1200.md"
