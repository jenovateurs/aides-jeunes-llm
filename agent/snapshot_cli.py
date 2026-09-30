"""Veille par snapshots de pages (lancement manuel). python -m agent.snapshot_cli

Compare le contenu utile de la page `link` de chaque fiche publique au snapshot
du run précédent. Si des dates, des montants ou une clôture changent, et que
les contrôles statiques passent, ouvre une PR (ou commente la PR ouverte) avec
le diff. Aucun appel LLM. Voir docs/superpowers/specs/2026-09-23-snapshot-veille-design.md.
"""
import argparse
import asyncio
from datetime import date, datetime
from pathlib import Path

import yaml as pyyaml

from agent.services import snapshot_report as report
from agent.services.pr import PRService
from agent.tools.benefit_loader import load_dispositifs
from agent.tools.http_client import HostThrottle, make_client
from agent.tools.page_snapshot import EXTRACTOR_VERSION, extract_main_text, normalize
from agent.tools.snapshot_diff import compute_diff
from agent.tools.snapshot_guard import check_diff, check_page
from agent.tools.snapshot_store import SnapshotStore, sha256_lines
from agent.tools.yaml_updater import add_header_comment, mark_private
from agent.tools.yaml_validator import validate_benefit
from configs.settings import settings

# Actions après lesquelles le snapshot du jour devient la nouvelle référence.
# Les autres (erreur, plafond, suspect, dry-run) laissent l'ancien : le diff
# sera rejoué au prochain run au lieu d'être perdu.
SAVED_ACTIONS = {"pr_off", "pr_opened", "commented", "already_posted", "blocked"}


async def http_fetch(client, throttle, semaphore, url: str) -> dict:
    """GET de la page : {status, content_type, final_url, html} ou {error}."""
    async with semaphore, throttle.slot(url):
        try:
            resp = await client.get(url)
        except Exception as exc:  # httpx.HTTPError, TLS, URL invalide…
            return {"error": f"{type(exc).__name__}: {exc}"}
    return {"status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "final_url": str(resp.url), "html": resp.text}


class SnapshotRunner:
    """Un run de veille snapshots. `fetch`, `pr_service`, `dispositifs`
    injectables (tests) ; sinon construits depuis la config."""

    def __init__(self, store: SnapshotStore, dispositifs=None, fetch=None,
                 pr_service=None, pr_mode=None, today: str | None = None,
                 reports_dir: Path | None = None, max_pr: int | None = None,
                 min_chars: int | None = None, max_churn: float | None = None,
                 diff_max_chars: int | None = None):
        self.store = store
        self.dispositifs = dispositifs
        self.fetch = fetch
        self.pr_service = pr_service
        self.pr_mode = pr_mode or settings.VEILLE_PR_MODE
        self.day = today or date.today().strftime("%Y%m%d")
        self.today_iso = report.iso(self.day)
        self.reports_dir = Path(reports_dir or settings.VEILLE_REPORTS_DIR)
        self.max_pr = settings.VEILLE_MAX_PR if max_pr is None else max_pr
        self.min_chars = settings.SNAPSHOT_MIN_CHARS if min_chars is None else min_chars
        self.max_churn = settings.SNAPSHOT_MAX_CHURN if max_churn is None else max_churn
        self.diff_max_chars = (settings.SNAPSHOT_DIFF_MAX_CHARS
                               if diff_max_chars is None else diff_max_chars)
        self.opened = 0

    def _pr(self) -> PRService:
        if self.pr_service is None:
            self.pr_service = PRService(
                settings.AIDES_JEUNES_ROOT,
                remote=settings.VEILLE_GIT_REMOTE,
                base_remote=settings.VEILLE_PR_BASE_REMOTE,
                base_repo=settings.VEILLE_PR_REPO,
                head_owner=settings.VEILLE_PR_HEAD,
            )
        return self.pr_service

    async def run(self, only=(), dry_run: bool = False, baseline: bool = False) -> dict:
        dispositifs = self.dispositifs
        if dispositifs is None:
            dispositifs = load_dispositifs(settings.AIDES_JEUNES_BENEFITS_PATH)
        if only:
            dispositifs = [d for d in dispositifs if d["slug"] in set(only)]

        if self.fetch is not None:
            results = await self._run_all(dispositifs, self.fetch, dry_run, baseline)
        else:
            throttle = HostThrottle()
            semaphore = asyncio.Semaphore(settings.VEILLE_CONCURRENCY)
            async with make_client() as client:
                async def fetch(url):
                    return await http_fetch(client, throttle, semaphore, url)
                results = await self._run_all(dispositifs, fetch, dry_run, baseline)

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        path = self.reports_dir / f"snapshot-{now.strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text(report.render_report(
            results, now.strftime("%Y-%m-%d %H:%M"), dry_run, self.pr_mode),
            encoding="utf-8")
        return {"results": results, "report_path": path}

    async def _run_all(self, dispositifs, fetch, dry_run, baseline) -> list[dict]:
        # Fetch concurrent (borné par la sémaphore et le throttle par hôte),
        # puis traitement séquentiel : git/gh ne supportent pas le parallèle.
        links = [d["yaml"].get("link") for d in dispositifs]
        pages = await asyncio.gather(*(
            fetch(link) if isinstance(link, str) and link else _none()
            for link in links))
        results = []
        for disp, link, fetched in zip(dispositifs, links, pages):
            res = await self._process(disp, link, fetched, fetch, dry_run, baseline)
            res.setdefault("slug", disp["slug"])
            res.setdefault("label", disp.get("label", disp["slug"]))
            print(f"{res['slug']}  {res['status']}  "
                  f"{report.trigger_labels(res.get('triggers') or {}) or '-'}  "
                  f"{res.get('action') or '-'}")
            results.append(res)
        return results

    def _lines(self, fetched: dict, link: str):
        """(lignes, None) ou (None, résultat d'échec) pour une page récupérée."""
        if fetched.get("error") or fetched.get("status") != 200:
            return None, {"status": "fetch_error",
                          "error": fetched.get("error") or f"HTTP {fetched.get('status')}"}
        if "html" not in (fetched.get("content_type") or "").lower():
            return None, {"status": "suspect", "reasons": ["non_html"]}
        text = extract_main_text(fetched.get("html") or "")
        if text is None:
            return None, {"status": "extract_error"}
        lines = normalize(text)
        reasons = check_page(fetched["content_type"], fetched.get("final_url") or link,
                             link, "\n".join(lines), self.min_chars)
        if reasons:
            return None, {"status": "suspect", "reasons": reasons}
        return lines, None

    async def _process(self, disp, link, fetched, fetch, dry_run, baseline) -> dict:
        slug = disp["slug"]
        if fetched is None:
            return {"status": "no_link"}
        lines, failure = self._lines(fetched, link)
        if failure:
            return failure

        def save():
            if not dry_run:
                self.store.save(slug, self.day, lines, url=link,
                                extractor=EXTRACTOR_VERSION)

        meta = self.store.load_meta(slug)
        prev = self.store.latest_before(slug, self.day)
        base_reasons = []
        if baseline:
            base_reasons.append("baseline_demandee")
        elif prev is None:
            base_reasons.append("premier_snapshot")
        else:
            if meta.get("url") != link:
                base_reasons.append("url_changee")
            if meta.get("extractor") != EXTRACTOR_VERSION:
                base_reasons.append("extracteur_change")
        if base_reasons:
            save()
            return {"status": "baseline", "reasons": base_reasons}

        result = compute_diff(prev["lines"], lines, self.today_iso)
        if not result["changed"]:
            save()
            return {"status": "unchanged"}
        if not result["triggers"]:
            save()
            return {"status": "changed"}

        out = {"status": "triggered", "triggers": result["triggers"],
               "strong_cloture": result["strong_cloture"]}
        reasons = check_diff(len("\n".join(prev["lines"])), len("\n".join(lines)),
                             result["churn"], self.max_churn)
        if not reasons:
            # Re-fetch : un bloc « actualités » tournant ou un A/B test donne
            # un contenu différent à chaque requête, pas un vrai changement.
            again, _ = self._lines(await fetch(link), link)
            if again is None or sha256_lines(again) != sha256_lines(lines):
                reasons = ["instable"]
        if reasons:
            return {**out, "status": "suspect", "reasons": reasons}

        out.update(await self._act(disp, prev, lines, result, dry_run))
        if out["action"] in SAVED_ACTIONS:
            save()
        return out

    async def _act(self, disp, prev, lines, result, dry_run) -> dict:
        """PR ou commentaire. Renvoie {action, pr_url?, error?}."""
        if self.pr_mode == "off":
            return {"action": "pr_off"}
        slug = disp["slug"]
        sha = sha256_lines(lines)
        try:
            pr = self._pr()
            if pr.list_prs() is None:
                # Sans liste des PR, pas de dédup fiable : on n'envoie rien.
                return {"action": "pr_error", "error": "gh indisponible"}
            comment = report.pr_comment(prev["date"], self.day, result,
                                        self.diff_max_chars)
            open_url = pr.find_open_pr(slug)
            if open_url:
                if self.store.load_meta(slug).get("last_posted") == {
                        "pr": open_url, "sha": sha}:
                    return {"action": "already_posted", "pr_url": open_url}
                if dry_run:
                    return {"action": "would_comment", "pr_url": open_url}
                pr.comment(open_url, comment)
                self.store.set_last_posted(slug, open_url, sha)
                return {"action": "commented", "pr_url": open_url}
            if pr.is_refused(slug):
                return {"action": "blocked"}
            if dry_run:
                return {"action": "would_open_pr"}
            if self.opened >= self.max_pr:
                return {"action": "pr_capped"}
            return self._open_pr(disp, prev, result, comment, sha)
        except Exception as exc:
            return {"action": "pr_error", "error": str(exc)}

    def _open_pr(self, disp, prev, result, comment, sha) -> dict:
        slug = disp["slug"]
        pr = self._pr()
        file_path, rel = _file_and_rel(disp)
        original = file_path.read_text(encoding="utf-8")
        yaml_diff = None
        if result["strong_cloture"]:
            yaml_diff = mark_private(file_path)
        else:
            add_header_comment(file_path, report.header_comment(result, self.today_iso))
        try:
            errors = validate_benefit(
                pyyaml.safe_load(file_path.read_text(encoding="utf-8")),
                existing_institution_slugs=[], existing_benefit_slugs=[])
        except Exception as exc:
            errors = [f"YAML illisible : {exc}"]
        if errors:
            file_path.write_text(original, encoding="utf-8")
            return {"action": "invalid_patch", "error": "; ".join(errors)}
        url = pr.create(slug, rel, report.pr_title(disp, result),
                        report.pr_body(disp, prev["date"], self.day, result, yaml_diff),
                        draft=(self.pr_mode == "draft"), today=self.day,
                        prefix="snapshot")
        self.opened += 1
        pr.comment(url, comment)
        self.store.set_last_posted(slug, url, sha)
        return {"action": "pr_opened", "pr_url": url}


async def _none():
    return None


def _file_and_rel(disp) -> tuple[Path, str]:
    """Fichier de la fiche + chemin relatif au repo aides-jeunes (pour git add)."""
    file_path = Path(disp["path"])
    try:
        rel = str(file_path.resolve().relative_to(
            Path(settings.AIDES_JEUNES_ROOT).resolve()))
    except ValueError:
        rel = f"data/benefits/{disp.get('dir') or 'javascript'}/{disp['slug']}.yml"
    return file_path, rel


def parse_args(argv) -> dict:
    parser = argparse.ArgumentParser(
        description="Veille snapshots — diff des pages `link` (lancement manuel)")
    parser.add_argument("--only", nargs="+", default=[],
                        help="Ne traite que ces slugs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="Diff + rapport seulement : ni YAML, ni PR, ni snapshot")
    mode.add_argument("--baseline", action="store_true",
                      help="Enregistre les snapshots sans comparer")
    ns = parser.parse_args(argv)
    return {"only": ns.only, "dry_run": ns.dry_run, "baseline": ns.baseline}


def main(argv=None):
    params = parse_args(argv)
    runner = SnapshotRunner(SnapshotStore(settings.SNAPSHOT_DIR,
                                          keep=settings.SNAPSHOT_KEEP))
    out = asyncio.run(runner.run(**params))
    print(f"Rapport : {out['report_path']}")
    return out


if __name__ == "__main__":
    main()
