"""Agent 3 — Veille : pipeline load_pending → check_links → check_content →
apply_updates_and_pr → generate_report. Orchestré pour le streaming SSE."""
import asyncio
import json
from pathlib import Path
from typing import Optional, TypedDict, Callable

import yaml as pyyaml

from configs.settings import settings
from agent.services.llm import LLMService
from agent.services.report import build_summary, render_markdown, write_report
from agent.services.pr import PRService
from agent.tools.benefit_loader import load_dispositifs as _load_dispositifs
from agent.tools.benefit_loader import load_covoiturage as _load_covoiturage
from agent.tools.priority_stats import fetch_priority_map
from agent.tools.veille_state import StateStore
from agent.tools.http_client import HostThrottle, make_client
from agent.tools.link_checker import check_dispositif_links, classify_status
from agent.tools.link_ignore import load_link_ignore
from agent.tools.content_check import fetch_page_text, check_content
from agent.tools.yaml_updater import (
    patch_benefit_file, mark_private, unmark_private)
from agent.tools.yaml_validator import validate_benefit


class VeilleState(TypedDict, total=False):
    limit: int
    only: list
    model_name: Optional[str]
    links_only: bool
    covoiturage: bool
    revival: bool
    all_private: bool
    dispositifs: list
    link_results: list
    content_results: list
    pr_results: list
    report_md: Optional[str]
    report_path: Optional[str]
    summary: dict
    error: Optional[str]


def _load_prompt(name: str = "veille") -> dict:
    path = Path(__file__).parent.parent / "prompts" / f"{name}.yaml"
    with open(path, encoding="utf-8") as fh:
        return pyyaml.safe_load(fh)


class VeilleAgent:
    """Pipeline de veille. Dépendances injectables pour les tests."""

    def __init__(
        self,
        load_dispositifs: Callable = _load_dispositifs,
        load_covoiturage: Callable = _load_covoiturage,
        fetch_priority: Callable = None,
        check_links: Callable = None,
        fetch_page: Callable = None,
        make_llm: Callable = None,
        pr_service: PRService = None,
        benefits_dir: Path = None,
        benefits_dirs: list = None,
        covoiturage_path: Path = None,
        state_path: Path = None,
        revival_state_path: Path = None,
        reports_dir: Path = None,
        stats_url: str = None,
        matomo_url: str = None,
        pr_mode: str = None,
        confidence_min: float = None,
        concurrency: int = None,
        daily_batch: int = None,
        recheck_days: int = None,
        today: str = None,
        timestamp: str = None,
        link_ignore=None,
        link_ignore_path: Path = None,
    ):
        # benefits_dir (dossier unique) reste accepté ; benefits_dirs permet de
        # scanner plusieurs dossiers (javascript/ + openfisca/).
        self.benefits_dirs = [Path(d) for d in (
            benefits_dirs or ([benefits_dir] if benefits_dir
                              else settings.VEILLE_BENEFITS_DIRS))]
        self.benefits_dir = self.benefits_dirs[0]
        self.state_path = Path(state_path or settings.VEILLE_STATE_PATH)
        # State du mode revival : séparé, pour ne pas écraser la rotation de la
        # veille normale (`last_run` partagé par slug).
        self.revival_state_path = Path(
            revival_state_path or settings.VEILLE_REVIVAL_STATE_PATH)
        self.reports_dir = Path(reports_dir or settings.VEILLE_REPORTS_DIR)
        self.stats_url = stats_url or settings.VEILLE_STATS_URL
        self.matomo_url = matomo_url or settings.VEILLE_MATOMO_URL
        self.pr_mode = pr_mode if pr_mode is not None else settings.VEILLE_PR_MODE
        self.confidence_min = confidence_min if confidence_min is not None else settings.VEILLE_CONFIDENCE_MIN
        self.concurrency = concurrency or settings.VEILLE_CONCURRENCY
        self.daily_batch = daily_batch or settings.VEILLE_DAILY_BATCH
        self.recheck_days = recheck_days or settings.VEILLE_RECHECK_DAYS
        self.today = today
        self.timestamp = timestamp
        self.pr_service = pr_service

        self._load_dispositifs = load_dispositifs
        self._load_covoiturage = load_covoiturage
        self.covoiturage_path = Path(
            covoiturage_path or settings.AIDES_JEUNES_COVOITURAGE_PATH)
        self._fetch_priority = fetch_priority or (
            lambda: fetch_priority_map(self.stats_url, self.matomo_url)
        )
        self._check_links = check_links
        self._fetch_page = fetch_page
        self._make_llm = make_llm or (lambda name: LLMService(
            model_name=name, force_gateway=True) if name else LLMService())
        self.ignore = link_ignore or load_link_ignore(
            link_ignore_path or settings.VEILLE_LINK_IGNORE_PATH)
        self.max_pr = settings.VEILLE_MAX_PR
        self.revival_batch = settings.VEILLE_REVIVAL_BATCH
        self.revival_exclude = settings.VEILLE_REVIVAL_EXCLUDE
        self.prompt = _load_prompt()
        # Prompt montant-only : en revival, `conditions` n'est pas fiable.
        self.revival_prompt = _load_prompt("revival")

    def _get_pr(self) -> PRService:
        """PRService injecté (tests) ou construit depuis la config."""
        return self.pr_service or PRService(
            settings.AIDES_JEUNES_ROOT,
            remote=settings.VEILLE_GIT_REMOTE,
            base_remote=settings.VEILLE_PR_BASE_REMOTE,
            base_repo=settings.VEILLE_PR_REPO,
            head_owner=settings.VEILLE_PR_HEAD,
        )

    async def _emit(self, emit, event_type, payload):
        if emit is not None:
            await emit(event_type, payload)

    async def run(self, params: dict, emit=None) -> dict:
        try:
            return await self._run(params, emit)
        except Exception as exc:
            await self._emit(emit, "error", {"message": str(exc)})
            return {"error": str(exc)}

    async def _run(self, params: dict, emit) -> dict:
        revival = bool(params.get("revival"))
        # Cap distinct : VEILLE_DAILY_BATCH (10) ramènerait un --limit 67 à 10.
        batch = self.revival_batch if revival else self.daily_batch
        limit = min(int(params.get("limit") or batch), batch)
        only = params.get("only") or []
        model_name = params.get("model_name")
        links_only = bool(params.get("links_only"))
        covoiturage = bool(params.get("covoiturage"))
        today = self.today or _iso_today()
        timestamp = self.timestamp or _now_stamp()

        # 1. load_pending
        if revival:
            all_disp = self._load_dispositifs(self.benefits_dirs, only_private=True)
            all_disp = [d for d in all_disp
                        if d["slug"] not in self.revival_exclude]
            if not params.get("all_private"):
                all_disp = self._traced_private(all_disp)
        else:
            all_disp = self._load_dispositifs(self.benefits_dirs)
            if covoiturage:
                all_disp = all_disp + self._load_covoiturage(self.covoiturage_path)
        priority = await _maybe_await(self._fetch_priority())
        store = StateStore(self.revival_state_path if revival else self.state_path,
                           today=today)
        dispositifs = store.select_pending(
            all_disp, priority, limit=limit,
            recheck_days=self.recheck_days, only=only or None,
        )
        total = len(dispositifs)
        await self._emit(emit, "progress", {"step": "load_pending", "total": total})

        # 2. check_links
        link_results = []
        semaphore = asyncio.Semaphore(self.concurrency)
        throttle = HostThrottle()
        async with make_client() as client:
            for i, disp in enumerate(dispositifs):
                if self._check_links:
                    res = await self._check_links(disp, client, semaphore)
                else:
                    res = await check_dispositif_links(
                        disp, client, semaphore, throttle)
                for l in res["links"]:
                    l["classe"] = ("ignored" if self.ignore.is_ignored(l["url"])
                                   else classify_status(l["status"]))
                link_results.append(res)
                await self._emit(emit, "progress", {
                    "step": "check_links", "slug": disp["slug"],
                    "index": i + 1, "total": total,
                    "status": "ok" if all(l["classe"] == "ok" for l in res["links"]) else "issue",
                })

            # 3. check_content (sauté en mode links_only : aucun appel LLM)
            content_results = []
            if links_only:
                for i, disp in enumerate(dispositifs):
                    content_results.append({"slug": disp["slug"], "stale": False,
                                            "confidence": 0.0, "divergences": [],
                                            "proposed": {}, "skipped": "links_only"})
                    await self._emit(emit, "progress", {
                        "step": "check_content", "slug": disp["slug"],
                        "index": i + 1, "total": total, "status": "skipped",
                    })
            else:
                llm = self._make_llm(model_name)
                alive_by_slug = {r["slug"]: _links_alive(r) for r in link_results}
                for i, disp in enumerate(dispositifs):
                    if revival and not alive_by_slug.get(disp["slug"]):
                        # Fiche non réactivable : inutile de payer un appel LLM.
                        content_results.append({"slug": disp["slug"], "stale": False,
                                                "confidence": 0.0, "divergences": [],
                                                "proposed": {}, "skipped": "still_broken"})
                        continue
                    if disp.get("check_only"):
                        # covoiturage : entrée JSON, pas de fiche à comparer.
                        content_results.append({"slug": disp["slug"], "stale": False,
                                                "confidence": 0.0, "divergences": [],
                                                "proposed": {}, "skipped": "check_only"})
                        continue
                    url = disp["yaml"].get("link")
                    page_text = None
                    if url:
                        page_text = (await self._fetch_page(url, client)
                                     if self._fetch_page
                                     else await fetch_page_text(url, client))
                    if not page_text:
                        content_results.append({"slug": disp["slug"], "stale": False,
                                                "confidence": 0.0, "divergences": [],
                                                "proposed": {}, "skipped": "unreachable"})
                    else:
                        content_results.append(await check_content(
                            disp, page_text, llm,
                            self.revival_prompt if revival else self.prompt,
                            allowed=("montant",) if revival else None))
                    await self._emit(emit, "progress", {
                        "step": "check_content", "slug": disp["slug"],
                        "index": i + 1, "total": total,
                        "status": "stale" if content_results[-1].get("stale") else "ok",
                    })

        # 4. apply_updates_and_pr
        apply = self._apply_revivals if revival else self._apply_updates
        pr_results = await apply(
            dispositifs, link_results, content_results, today, emit)

        # 5. generate_report
        summary = build_summary(link_results, content_results, pr_results)
        report_md = render_markdown(link_results, content_results, pr_results,
                                    summary, generated_at=today,
                                    mode="revival" if revival else "veille")
        report_path = write_report(report_md, self.reports_dir, timestamp,
                                   prefix="revival" if revival else "veille")

        # 6. persist state
        self._mark_state(store, link_results, content_results, pr_results)
        store.save()

        payload = {
            "report_path": str(report_path),
            "summary": summary,
            "broken_links": [l for r in link_results for l in r["links"] if not l["ok"]],
            "stale_content": [c for c in content_results if c.get("stale")],
            "prs": pr_results,
        }
        await self._emit(emit, "done", payload)
        return {"report_md": report_md, "report_path": str(report_path),
                "summary": summary, "link_results": link_results,
                "content_results": content_results, "pr_results": pr_results}

    async def _apply_updates(self, dispositifs, link_results, content_results, today, emit):
        by_slug = {d["slug"]: d for d in dispositifs}
        results = []
        handled = set()
        opened = 0  # PR réellement envoyées ce run (cap VEILLE_MAX_PR)

        def _capped():
            return self.pr_mode != "off" and opened >= self.max_pr

        # 4a. Liens vraiment cassés → PR passage en private (précédence).
        for lr in link_results:
            slug = lr["slug"]
            broken = [l for l in lr["links"] if l.get("classe") == "broken"]
            if not broken:
                continue
            handled.add(slug)
            if self.pr_mode == "off" or by_slug[slug].get("check_only"):
                # check_only (covoiturage) : pas de fiche YAML à patcher → rapport seul.
                results.append({"slug": slug, "action": "broken_no_pr",
                                "broken": [l["url"] for l in broken]})
            elif _capped():
                results.append({"slug": slug, "action": "pr_capped"})
            else:
                res = await self._open_broken_pr(by_slug[slug], broken, today)
                if res.get("action") == "pr_opened":
                    opened += 1
                results.append(res)
            await self._emit(emit, "pr", results[-1])

        # 4b. Contenu obsolète → PR maj montant/conditions (sauf si déjà en private PR).
        for c in content_results:
            slug = c["slug"]
            if slug in handled:
                continue
            if not (c.get("stale") and c.get("proposed")
                    and c.get("confidence", 0.0) >= self.confidence_min):
                continue
            if self.pr_mode == "off" or by_slug[slug].get("check_only"):
                results.append({"slug": slug, "action": "proposed_no_pr"})
            elif _capped():
                results.append({"slug": slug, "action": "pr_capped"})
            else:
                res = await self._open_pr(by_slug[slug], c, today)
                if res.get("action") == "pr_opened":
                    opened += 1
                results.append(res)
            await self._emit(emit, "pr", results[-1])
        return results

    def _traced_private(self, dispositifs) -> list:
        """Fiches private que la veille a elle-même mises en cause.

        Sur les fiches `private`, seul le state de la veille distingue "private
        car lien mort" de "private par décision métier" (dispositif terminé,
        jamais lancé, saisonnier). Sans cette trace, un lien redevenu 200 ne
        dit rien : réactiver produirait des PR bruyantes. `--all-private`
        contourne ce filtre en assumant ce bruit.
        """
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        benefits = data.get("benefits", {}) or {}
        traced = {
            slug for slug, entry in benefits.items()
            if entry.get("pr_url") or entry.get("link_status") == "broken"
        }
        return [d for d in dispositifs if d["slug"] in traced]

    async def _apply_revivals(self, dispositifs, link_results, content_results,
                              today, emit):
        """Mode revival : PR de sortie du private quand tous les liens répondent."""
        by_slug = {d["slug"]: d for d in dispositifs}
        content_by_slug = {c["slug"]: c for c in content_results}
        results = []
        opened = 0

        for lr in link_results:
            slug = lr["slug"]
            if not lr["links"]:
                # Fiche sans lien : rien à retester, aucune preuve de vie.
                results.append({"slug": slug, "action": "no_link"})
            elif not _links_alive(lr):
                results.append({
                    "slug": slug, "action": "still_broken",
                    "broken": [l["url"] for l in lr["links"]
                               if l.get("classe") not in ("ok", "ignored")],
                })
            elif self.pr_mode == "off" or by_slug[slug].get("check_only"):
                results.append({"slug": slug, "action": "revive_no_pr"})
            elif opened >= self.max_pr:
                results.append({"slug": slug, "action": "pr_capped"})
            else:
                res = await self._open_revive_pr(
                    by_slug[slug], lr, content_by_slug.get(slug, {}), today)
                if res.get("action") == "pr_opened":
                    opened += 1
                results.append(res)
            await self._emit(emit, "pr", results[-1])
        return results

    async def _open_revive_pr(self, disp, link_result, content, today) -> dict:
        slug = disp["slug"]
        pr = self._get_pr()
        file_path, rel = self._file_and_rel(disp)
        try:
            if pr.branch_exists(slug):
                return {"slug": slug, "action": "pr_exists"}
            diff = unmark_private(file_path)
            proposed = content.get("proposed") or {}
            montant_diff = None
            if ("montant" in proposed
                    and content.get("confidence", 0.0) >= self.confidence_min):
                montant_diff = patch_benefit_file(
                    file_path, {"montant": proposed["montant"]})
            title = (f"veille: {disp.get('label', slug)} — lien(s) rétabli(s), "
                     "sortie du mode private")
            body = _pr_body_revive(disp, link_result, content, diff, montant_diff)
            url = pr.create(slug, rel, title, body,
                            draft=(self.pr_mode == "draft"),
                            today=today.replace("-", ""), prefix="revive")
            return {"slug": slug, "action": "pr_opened", "kind": "revive",
                    "pr_url": url}
        except Exception as exc:
            return {"slug": slug, "action": "pr_error", "error": str(exc)}

    def _file_and_rel(self, disp) -> tuple:
        """Fichier source de la fiche + son chemin relatif au repo aides-jeunes.

        `path`/`dir` viennent du loader ; fallback sur le premier dossier scanné
        pour les dispositifs injectés (tests) qui ne les portent pas.
        """
        file_path = Path(disp.get("path") or (self.benefits_dir / f"{disp['slug']}.yml"))
        try:
            rel = str(file_path.resolve().relative_to(
                Path(settings.AIDES_JEUNES_ROOT).resolve()))
        except ValueError:
            folder = disp.get("dir") or self.benefits_dir.name
            rel = f"data/benefits/{folder}/{disp['slug']}.yml"
        return file_path, rel

    async def _open_broken_pr(self, disp, broken, today) -> dict:
        slug = disp["slug"]
        pr = self._get_pr()
        file_path, rel = self._file_and_rel(disp)
        try:
            if pr.branch_exists(slug):
                return {"slug": slug, "action": "pr_exists"}
            diff = mark_private(file_path)
            title = f"veille: {disp.get('label', slug)} — lien(s) cassé(s), passage en private"
            body = _pr_body_broken(disp, broken, diff)
            url = pr.create(slug, rel, title, body,
                            draft=(self.pr_mode == "draft"),
                            today=today.replace("-", ""))
            return {"slug": slug, "action": "pr_opened", "kind": "private", "pr_url": url}
        except Exception as exc:
            return {"slug": slug, "action": "pr_error", "error": str(exc)}

    async def _open_pr(self, disp, content, today) -> dict:
        slug = disp["slug"]
        pr = self._get_pr()
        file_path, rel = self._file_and_rel(disp)
        try:
            if pr.branch_exists(slug):
                return {"slug": slug, "action": "pr_exists"}
            diff = patch_benefit_file(file_path, content["proposed"])
            try:
                # Valide la fiche patchée réelle sur disque (ce qui sera committé).
                patched = pyyaml.safe_load(file_path.read_text(encoding="utf-8"))
                errors = validate_benefit(
                    patched,
                    existing_institution_slugs=[], existing_benefit_slugs=[],
                )
            except Exception:
                errors = []  # validation best-effort, non bloquante
            if errors:
                return {"slug": slug, "action": "invalid_patch", "error": str(errors)}
            champs = ", ".join(content["proposed"].keys())
            title = f"veille: mise à jour {disp.get('label', slug)} ({champs})"
            body = _pr_body(disp, content, diff)
            url = pr.create(slug, rel, title, body,
                            draft=(self.pr_mode == "draft"),
                            today=today.replace("-", ""))
            return {"slug": slug, "action": "pr_opened", "pr_url": url}
        except Exception as exc:
            return {"slug": slug, "action": "pr_error", "error": str(exc)}

    def _mark_state(self, store, link_results, content_results, pr_results):
        pr_by_slug = {p["slug"]: p for p in pr_results}
        content_by_slug = {c["slug"]: c for c in content_results}
        for lr in link_results:
            slug = lr["slug"]
            classes = {l.get("classe", "ok") for l in lr["links"]}
            if "broken" in classes:
                link_status = "broken"
            elif "suspicious" in classes:
                link_status = "suspicious"
            elif classes and classes != {"ok"} and classes != {"ignored"} and classes != {"ok", "ignored"}:
                link_status = "mixed"
            else:
                link_status = "ok"
            c = content_by_slug.get(slug, {})
            content_status = ("stale" if c.get("stale")
                              else "skipped" if c.get("skipped")
                              else "no_change")
            store.mark(slug, link_status=link_status, content_status=content_status,
                       pr_url=pr_by_slug.get(slug, {}).get("pr_url"))


def _links_alive(link_result) -> bool:
    """Vrai si tous les liens de la fiche répondent (ok, ou faux positif connu).

    Strict volontairement : un seul lien `broken` ou `suspicious` suffit à ne
    pas réactiver. Sur un doute, on laisse la fiche en private.
    """
    links = link_result.get("links") or []
    return bool(links) and all(
        l.get("classe") in ("ok", "ignored") for l in links)


def _pr_body_revive(disp, link_result, content, diff, montant_diff) -> str:
    lines = [f"## Dispositif : {disp.get('label', disp['slug'])}",
             f"- Institution : {disp.get('institution', '')}", "",
             "### Lien(s) re-testé(s)", ""]
    for l in link_result.get("links", []):
        etat = "ignoré (faux positif connu)" if l.get("classe") == "ignored" else "OK"
        lines.append(f"- [{l['type']}] {l['url']} → **{l['status']}** ({etat})")
    lines += ["", "### Action proposée",
              f"- `private` : `{diff['before'].get('private')}` → retiré"]
    if montant_diff:
        lines.append(
            f"- `montant` (maximum) : `{montant_diff['before'].get('montant')}` → "
            f"`{montant_diff['after'].get('montant')}`")
    elif content.get("proposed", {}).get("montant") is not None:
        lines.append(
            f"- `montant` : divergence détectée "
            f"(page ≈ `{content['proposed']['montant']}`) mais confiance "
            f"{content.get('confidence')} trop faible → **non modifié**, à vérifier")
    else:
        lines.append("- `montant` (maximum) : inchangé")
    for d in content.get("divergences", []):
        lines.append(f"  - extrait page : > {d.get('extrait_source', '')}")
    lines += ["",
              "> Le/les lien(s) de ce dispositif répondent à nouveau : la fiche "
              "sort du mode `private`. **À vérifier manuellement** : un lien "
              "vivant ne garantit pas que le dispositif existe encore (page "
              "d'accueil rétablie, dispositif clos, campagne terminée). Si l'aide "
              "n'existe plus, fermer cette PR — le dispositif ne sera plus "
              "reproposé.",
              "",
              "> PR générée automatiquement par l'agent Veille (aj-llm) — à valider par un humain."]
    return "\n".join(lines)


def _pr_body_broken(disp, broken, diff) -> str:
    lines = [f"## Dispositif : {disp.get('label', disp['slug'])}",
             f"- Institution : {disp.get('institution', '')}", "",
             "### Lien(s) cassé(s) détecté(s)", ""]
    for l in broken:
        lines.append(f"- [{l['type']}] {l['url']} → **{l['status']}** — à vérifier")
    lines += ["", "### Action proposée",
              f"- `private` : `{diff['before'].get('private')}` → `true`",
              "",
              "> Le dispositif est passé en `private: true` car son/ses lien(s) "
              "semblent morts. **À vérifier manuellement** : si le lien est en fait "
              "valide (protection anti-bot), rejeter cette PR et ajouter le domaine "
              "à `veille-link-ignore.yml`.",
              "",
              "> PR générée automatiquement par l'agent Veille (aj-llm) — à valider par un humain."]
    return "\n".join(lines)


def _pr_body(disp, content, diff) -> str:
    lines = [f"## Dispositif : {disp.get('label', disp['slug'])}",
             f"- Institution : {disp.get('institution', '')}",
             f"- Lien source : {disp['yaml'].get('link', '')}", "",
             "### Changements proposés", "",
             "| Champ | Avant | Après |", "|---|---|---|"]
    for champ in content["proposed"]:
        lines.append(f"| {champ} | {diff['before'].get(champ)} | {diff['after'].get(champ)} |")
    lines += ["", "### Extraits sources"]
    for d in content.get("divergences", []):
        lines.append(f"- **{d['champ']}** : > {d.get('extrait_source', '')}")
    lines += ["", f"_Confiance LLM : {content.get('confidence')}_",
              "", "> PR générée automatiquement par l'agent Veille (aj-llm) — à valider par un humain."]
    return "\n".join(lines)


async def _maybe_await(v):
    if asyncio.iscoroutine(v):
        return await v
    return v


def _iso_today() -> str:
    from datetime import date
    return date.today().isoformat()


def _now_stamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d-%H%M%S")


veille_agent = VeilleAgent()
