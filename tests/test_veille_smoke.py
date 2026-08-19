"""Smoke tests Agent 3 Veille : pipeline bout-en-bout + route SSE + tools clés."""
import asyncio
import json
import socket
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.tools.benefit_loader import extract_links, load_dispositifs
from agent.tools.veille_state import StateStore
from agent.tools.link_checker import check_link
from agent.tools.yaml_updater import patch_benefit_file
from agent.tools.content_check import (
    html_to_text, filter_divergences, coerce_montant)
from agent.tools.link_checker import (
    NETWORK_FATAL, NETWORK_TRANSIENT, classify_error, classify_status)
from agent.tools.http_client import BROWSER_HEADERS, HostThrottle, make_client
from agent.tools.link_ignore import LinkIgnore, load_link_ignore
from agent.tools.yaml_updater import mark_private
from agent.agents.veille import VeilleAgent


# ── tools ────────────────────────────────────────────────────────────────

def test_extract_links_dedup():
    b = {"link": "https://a.fr", "instructions": "https://a.fr",
         "form": "https://b.fr", "teleservice": 1}
    links = {l["url"]: l["type"] for l in extract_links(b)}
    assert links == {"https://a.fr": "link / instructions", "https://b.fr": "form"}


def test_load_dispositifs_public_only(tmp_path):
    (tmp_path / "pub.yml").write_text("label: P\nlink: https://a.fr\n", encoding="utf-8")
    (tmp_path / "priv.yml").write_text("label: X\nprivate: true\n", encoding="utf-8")
    out = load_dispositifs(tmp_path)
    assert [d["slug"] for d in out] == ["pub"]


def test_state_select_and_mark(tmp_path):
    store = StateStore(tmp_path / "s.json", today="2026-07-15")
    disp = [{"slug": "a"}, {"slug": "b"}]
    picked = store.select_pending(disp, {"a": 1, "b": 9}, limit=1, recheck_days=30)
    assert [d["slug"] for d in picked] == ["b"]
    store.mark("b", "ok", "stale", "http://pr/1")
    store.save()
    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["benefits"]["b"]["pr_url"] == "http://pr/1"


def test_check_link_status():
    async def run():
        t = httpx.MockTransport(lambda r: httpx.Response(404))
        async with httpx.AsyncClient(transport=t) as c:
            return await check_link("https://a.fr", c)
    out = asyncio.run(run())
    assert out["status"] == 404 and out["ok"] is False


def test_patch_preserves_comment(tmp_path):
    f = tmp_path / "b.yml"
    f.write_text("montant: 109\n# keep\nunit: €\n", encoding="utf-8")
    diff = patch_benefit_file(f, {"montant": 120})
    txt = f.read_text(encoding="utf-8")
    assert "montant: 120" in txt and "# keep" in txt
    assert diff["before"]["montant"] == 109


def test_html_to_text_and_filter():
    assert "Bonjour" in html_to_text("<p>Bonjour <b>x</b></p>")
    r = filter_divergences({"stale": True, "divergences": [
        {"champ": "montant", "extrait_source": ""}], "proposed": {"montant": 1}})
    assert r["stale"] is False and r["proposed"] == {}


def test_coerce_montant():
    assert coerce_montant(2280) == 2280
    assert coerce_montant("2 280") == 2280
    assert coerce_montant("Variable entre 150 € et 2 280 € selon durée") is None
    assert coerce_montant(None) is None
    assert coerce_montant(True) is None


def test_filter_drops_textual_montant():
    r = filter_divergences({"stale": True, "divergences": [
        {"champ": "montant", "extrait_source": "entre 150 € et 2 280 €"}],
        "proposed": {"montant": "entre 150 € et 2 280 € selon la durée"}})
    assert r["stale"] is False and r["proposed"] == {}


def test_filter_keeps_conditions_when_montant_dropped():
    r = filter_divergences({"stale": True, "divergences": [
        {"champ": "montant", "extrait_source": "de 150 à 2 280 €"},
        {"champ": "conditions", "extrait_source": "quotient familial < 800"}],
        "proposed": {"montant": "de 150 à 2 280 €",
                     "conditions": ["quotient familial inférieur à 800"]}})
    assert r["stale"] is True
    assert r["proposed"] == {"conditions": ["quotient familial inférieur à 800"]}
    assert [d["champ"] for d in r["divergences"]] == ["conditions"]


def test_classify_status():
    assert classify_status(200) == "ok"
    assert classify_status(404) == "broken"
    assert classify_status(410) == "broken"
    assert classify_status(500) == "broken"
    assert classify_status(403) == "suspicious"
    assert classify_status(429) == "suspicious"
    # réseau : transitoire ≠ définitif (un timeout ressemble à de l'anti-bot)
    assert classify_status(NETWORK_TRANSIENT) == "suspicious"
    assert classify_status(NETWORK_FATAL) == "broken"


def test_classify_error_transient_vs_fatal():
    req = httpx.Request("GET", "https://a.fr")
    dns = httpx.ConnectError("nodename nor servname provided")
    dns.__cause__ = socket.gaierror(8, "nodename nor servname provided")
    refused = httpx.ConnectError("refused")
    refused.__cause__ = ConnectionRefusedError(61, "Connection refused")

    assert classify_error(dns) == NETWORK_FATAL
    assert classify_error(refused) == NETWORK_FATAL
    assert classify_error(httpx.UnsupportedProtocol("nope")) == NETWORK_FATAL
    assert classify_error(httpx.ReadTimeout("slow", request=req)) == NETWORK_TRANSIENT
    assert classify_error(httpx.ConnectTimeout("slow")) == NETWORK_TRANSIENT
    assert classify_error(httpx.RemoteProtocolError("reset")) == NETWORK_TRANSIENT
    assert classify_error(httpx.ConnectError("tls handshake")) == NETWORK_TRANSIENT


def test_check_link_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("agent.tools.link_checker.BACKOFF_SECONDS", (0.0, 0.0))
    calls = []

    def handler(request):
        calls.append(request.url)
        if len(calls) < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await check_link("https://a.fr", c)

    out = asyncio.run(run())
    assert out["ok"] is True and len(calls) == 3


def test_check_link_no_retry_on_404(monkeypatch):
    monkeypatch.setattr("agent.tools.link_checker.BACKOFF_SECONDS", (0.0, 0.0))
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await check_link("https://a.fr", c)

    out = asyncio.run(run())
    assert out["status"] == 404 and len(calls) == 1


def test_check_link_dns_failure_is_fatal(monkeypatch):
    monkeypatch.setattr("agent.tools.link_checker.BACKOFF_SECONDS", (0.0, 0.0))

    def handler(request):
        exc = httpx.ConnectError("nodename nor servname provided")
        exc.__cause__ = socket.gaierror(8, "nodename nor servname provided")
        raise exc

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await check_link("https://mort.fr", c)

    out = asyncio.run(run())
    assert out["status"] == NETWORK_FATAL
    assert classify_status(out["status"]) == "broken"


def test_host_throttle_serializes_same_host():
    async def run():
        throttle = HostThrottle(delay=0.05, jitter=0.0)
        stamps = []

        async def hit(url):
            async with throttle.slot(url):
                stamps.append((url, asyncio.get_running_loop().time()))

        await asyncio.gather(hit("https://a.fr/1"), hit("https://a.fr/2"))
        return stamps

    stamps = asyncio.run(run())
    assert abs(stamps[1][1] - stamps[0][1]) >= 0.05


def test_host_throttle_parallel_across_hosts():
    async def run():
        throttle = HostThrottle(delay=0.2, jitter=0.0)
        start = asyncio.get_running_loop().time()

        async def hit(url):
            async with throttle.slot(url):
                pass

        await asyncio.gather(hit("https://a.fr/1"), hit("https://b.fr/1"))
        return asyncio.get_running_loop().time() - start

    assert asyncio.run(run()) < 0.2


def test_make_client_uses_http2_and_bot_ua():
    async def run():
        async with make_client() as c:
            return c
    client = asyncio.run(run())
    assert client.headers["accept-language"].startswith("fr-FR")
    assert "Firefox" in BROWSER_HEADERS["User-Agent"]
    # Un UA mentionnant un bot est filtré par DataDome (mesuré sur sncf.com).
    assert "bot" not in BROWSER_HEADERS["User-Agent"].lower()


def test_link_ignore_domain_and_subdomain():
    ig = LinkIgnore(domains=["ter.sncf.com"], urls=["https://x.fr/exact"])
    assert ig.is_ignored("https://www.ter.sncf.com/a/b") is True
    assert ig.is_ignored("https://ter.sncf.com/") is True
    assert ig.is_ignored("https://x.fr/exact") is True
    assert ig.is_ignored("https://autre.fr/") is False


def test_load_link_ignore_missing(tmp_path):
    ig = load_link_ignore(tmp_path / "nope.yml")
    assert ig.is_ignored("https://a.fr") is False


def test_mark_private(tmp_path):
    f = tmp_path / "b.yml"
    f.write_text("label: A\nlink: https://a.fr\n", encoding="utf-8")
    diff = mark_private(f)
    assert "private: true" in f.read_text(encoding="utf-8")
    assert diff["after"] == {"private": True}


# ── pipeline ─────────────────────────────────────────────────────────────

class _FakeLLM:
    async def generate_json(self, messages):
        return {"stale": True, "confidence": 0.95,
                "divergences": [{"champ": "montant", "valeur_fiche": 109,
                                 "valeur_page": 120, "extrait_source": "120 €"}],
                "proposed": {"montant": 120}}


class _FakePR:
    def __init__(self):
        self.created = []
    def branch_exists(self, slug):
        return False
    def create(self, slug, file_rel_path, title, body, draft, today):
        self.created.append((slug, draft))
        return f"http://pr/{slug}"


def _agent(tmp_path, pr_mode, pr=None):
    disp = [{"slug": "a", "label": "A", "institution": "i",
             "links": [{"url": "https://a.fr", "type": "link"}],
             "yaml": {"montant": 109, "conditions": ["x"], "description": "d",
                      "link": "https://a.fr"}}]

    async def fake_links(dsp, client, sem):
        return {"slug": dsp["slug"],
                "links": [{"url": l["url"], "type": l["type"], "status": 200, "ok": True}
                          for l in dsp["links"]]}

    async def fake_page(url, client):
        return "120 € texte page"

    # fiche réelle sur disque (le patch YAML l'exige en mode PR)
    benefits_dir = tmp_path / "benefits"
    benefits_dir.mkdir(parents=True, exist_ok=True)
    (benefits_dir / "a.yml").write_text(
        "label: A\ninstitution: i\nmontant: 109\nconditions:\n  - x\n"
        "description: d\ntype: float\nperiodicite: ponctuelle\n"
        "link: https://a.fr\n", encoding="utf-8")

    return VeilleAgent(
        load_dispositifs=lambda d: disp,
        fetch_priority=lambda: {"a": 10.0},
        check_links=fake_links,
        fetch_page=fake_page,
        make_llm=lambda name: _FakeLLM(),
        pr_service=pr,
        benefits_dir=tmp_path / "benefits",
        state_path=tmp_path / "state.json",
        reports_dir=tmp_path / "reports",
        pr_mode=pr_mode, confidence_min=0.8,
        today="2026-07-15", timestamp="20260715-1200",
    )


def test_pipeline_off_no_pr(tmp_path):
    events = []
    async def emit(t, p): events.append(t)
    agent = _agent(tmp_path, "off")
    state = asyncio.run(agent.run({"limit": 10, "only": [], "model_name": None}, emit=emit))
    assert state["summary"]["stale"] == 1
    assert state["summary"]["prs_opened"] == 0
    assert Path(state["report_path"]).exists()
    assert "done" in events
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["benefits"]["a"]["last_run"] == "2026-07-15"


def test_pipeline_draft_opens_pr(tmp_path):
    pr = _FakePR()
    agent = _agent(tmp_path, "draft", pr=pr)
    state = asyncio.run(agent.run({"limit": 10, "only": [], "model_name": None}))
    assert state["summary"]["prs_opened"] == 1
    assert pr.created == [("a", True)]


def _agent_broken(tmp_path, pr_mode, pr, ignore):
    # dispositif "a" : lien 404 (cassé) ; dispositif "b" : lien 403 ignoré (SNCF)
    disp = [
        {"slug": "a", "label": "A", "institution": "i",
         "links": [{"url": "https://dead.fr/x", "type": "link"}],
         "yaml": {"description": "d", "link": "https://dead.fr/x"}},
        {"slug": "b", "label": "B", "institution": "i",
         "links": [{"url": "https://ter.sncf.com/y", "type": "link"}],
         "yaml": {"description": "d", "link": "https://ter.sncf.com/y"}},
    ]
    status_map = {"https://dead.fr/x": 404, "https://ter.sncf.com/y": 403}

    async def fake_links(dsp, client, sem):
        return {"slug": dsp["slug"],
                "links": [{"url": l["url"], "type": l["type"],
                           "status": status_map[l["url"]], "ok": False}
                          for l in dsp["links"]]}

    async def fake_page(url, client):
        return None  # pas de content-check ici

    bd = tmp_path / "benefits"
    bd.mkdir(parents=True, exist_ok=True)
    (bd / "a.yml").write_text("label: A\ninstitution: i\ndescription: d\n"
                              "link: https://dead.fr/x\n", encoding="utf-8")
    (bd / "b.yml").write_text("label: B\ninstitution: i\ndescription: d\n"
                              "link: https://ter.sncf.com/y\n", encoding="utf-8")

    return VeilleAgent(
        load_dispositifs=lambda d: disp,
        fetch_priority=lambda: {"a": 2.0, "b": 1.0},
        check_links=fake_links, fetch_page=fake_page,
        make_llm=lambda name: _FakeLLM(),
        pr_service=pr, link_ignore=ignore,
        benefits_dir=bd, state_path=tmp_path / "state.json",
        reports_dir=tmp_path / "reports",
        pr_mode=pr_mode, confidence_min=0.8,
        today="2026-07-15", timestamp="20260715-1200",
    )


def test_broken_link_opens_private_pr_ignores_sncf(tmp_path):
    pr = _FakePR()
    ignore = LinkIgnore(domains=["ter.sncf.com"])
    agent = _agent_broken(tmp_path, "draft", pr, ignore)
    state = asyncio.run(agent.run({"limit": 10, "only": [], "model_name": None}))
    s = state["summary"]
    assert s["broken_links"] == 1          # dead.fr
    assert s["ignored_links"] == 1         # ter.sncf.com ignoré
    assert s["prs_opened"] == 1            # seulement "a"
    assert pr.created == [("a", True)]
    # fiche a passée en private, b intacte
    a_yml = (tmp_path / "benefits" / "a.yml").read_text(encoding="utf-8")
    b_yml = (tmp_path / "benefits" / "b.yml").read_text(encoding="utf-8")
    assert "private: true" in a_yml
    assert "private" not in b_yml


def test_max_pr_cap_stops(tmp_path):
    pr = _FakePR()
    agent = _agent_broken(tmp_path, "draft", pr, LinkIgnore(domains=["ter.sncf.com"]))
    agent.max_pr = 0  # cap à 0 → aucune PR envoyée
    state = asyncio.run(agent.run({"limit": 10, "only": [], "model_name": None}))
    assert state["summary"]["prs_opened"] == 0
    assert pr.created == []
    assert any(p["action"] == "pr_capped" for p in state["pr_results"])


def test_suspicious_403_no_pr_no_ignore(tmp_path):
    # 403 sans ignore → suspicious, pas de PR
    pr = _FakePR()
    agent = _agent_broken(tmp_path, "draft", pr, LinkIgnore())
    state = asyncio.run(agent.run({"limit": 10, "only": [], "model_name": None}))
    s = state["summary"]
    assert s["broken_links"] == 1          # dead.fr
    assert s["suspicious_links"] == 1      # sncf 403 non ignoré → suspect
    assert s["prs_opened"] == 1            # seulement dead.fr


def test_links_only_skips_content_check(tmp_path):
    # --links-only : aucun appel LLM, aucun fetch de page, aucune PR de contenu
    def _boom(name):
        raise AssertionError("LLM ne doit pas être instancié en mode links_only")

    agent = _agent(tmp_path, "draft", pr=_FakePR())
    agent._make_llm = _boom
    state = asyncio.run(agent.run(
        {"limit": 10, "only": [], "model_name": None, "links_only": True}))
    assert state["summary"]["stale"] == 0
    assert state["summary"]["prs_opened"] == 0
    assert state["pr_results"] == []
    assert all(c["skipped"] == "links_only" for c in state["content_results"])
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["benefits"]["a"]["content_status"] == "skipped"


def test_links_only_still_opens_broken_link_pr(tmp_path):
    pr = _FakePR()
    agent = _agent_broken(tmp_path, "draft", pr, LinkIgnore(domains=["ter.sncf.com"]))
    agent._make_llm = lambda name: (_ for _ in ()).throw(
        AssertionError("LLM ne doit pas être instancié en mode links_only"))
    state = asyncio.run(agent.run(
        {"limit": 10, "only": [], "model_name": None, "links_only": True}))
    assert state["summary"]["broken_links"] == 1
    assert state["summary"]["prs_opened"] == 1
    assert pr.created == [("a", True)]


def test_parse_args_links_only():
    from agent.veille_cli import parse_args

    assert parse_args([])["links_only"] is False
    assert parse_args(["--links-only"])["links_only"] is True


# ── route ────────────────────────────────────────────────────────────────

def test_route_streams_sse(monkeypatch):
    import backend.routes.veille as vr
    from backend.main import app

    async def fake_run(params, emit=None):
        await emit("progress", {"step": "load_pending", "total": 1})
        await emit("done", {"report_path": "/tmp/r.md", "summary": {"stale": 0}})
        return {"summary": {"stale": 0}}

    monkeypatch.setattr(vr.veille_agent, "run", fake_run)
    client = TestClient(app)
    with client.stream("POST", "/api/veille/run", json={"limit": 1}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = "".join(resp.iter_text())
    assert "event: progress" in body and "event: done" in body
    assert "load_pending" in body
