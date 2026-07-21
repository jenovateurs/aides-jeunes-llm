"""Rendu du rapport de veille (Markdown + résumé)."""
from pathlib import Path


def _classe(link) -> str:
    """Classe d'un lien : ok | broken | suspicious | ignored (fallback via ok)."""
    return link.get("classe") or ("ok" if link.get("ok") else "broken")


def build_summary(link_results, content_results, pr_results) -> dict:
    def count(classe):
        return sum(1 for r in link_results for l in r["links"] if _classe(l) == classe)

    stale = sum(1 for c in content_results if c.get("stale"))
    prs_opened = sum(1 for p in pr_results if p.get("action") == "pr_opened")
    proposals_no_pr = sum(
        1 for p in pr_results
        if p.get("action") in ("proposed_no_pr", "broken_no_pr", "pr_error",
                                "invalid_patch", "pr_exists", "pr_capped")
    )
    return {
        "checked": len(link_results),
        "broken_links": count("broken"),
        "suspicious_links": count("suspicious"),
        "ignored_links": count("ignored"),
        "stale": stale,
        "prs_opened": prs_opened,
        "proposals_no_pr": proposals_no_pr,
    }


def render_markdown(link_results, content_results, pr_results, summary, generated_at) -> str:
    lines = [f"# Rapport de veille — {generated_at}", ""]
    lines.append(
        f"- Dispositifs vérifiés : {summary['checked']}\n"
        f"- Liens cassés (→ private) : {summary['broken_links']}\n"
        f"- Liens suspects (vérif humaine) : {summary.get('suspicious_links', 0)}\n"
        f"- Liens ignorés (faux positifs connus) : {summary.get('ignored_links', 0)}\n"
        f"- Contenus divergents : {summary['stale']}\n"
        f"- PRs ouvertes : {summary['prs_opened']}\n"
        f"- Propositions sans PR : {summary['proposals_no_pr']}"
    )

    def _section(title, classe, empty_msg):
        out = ["", title, ""]
        found = False
        for r in link_results:
            for l in r["links"]:
                if _classe(l) == classe:
                    found = True
                    out.append(f"- `{r['slug']}` [{l['type']}] {l['url']} → {l['status']}")
        if not found:
            out.append(empty_msg)
        return out

    lines += _section("## Liens cassés (passage en private)", "broken", "_Aucun._")
    lines += _section(
        "## Liens suspects (protection anti-bot ? — faux négatif possible, vérif humaine)",
        "suspicious",
        "_Aucun._ Ajoute les domaines confirmés valides à `veille-link-ignore.yml`.",
    )
    lines += _section("## Liens ignorés (faux positifs connus)", "ignored", "_Aucun._")

    lines += ["", "## Contenus divergents", ""]
    stale_any = False
    for c in content_results:
        if not c.get("stale"):
            continue
        stale_any = True
        lines.append(f"### `{c['slug']}` (confiance {c.get('confidence')})")
        for d in c.get("divergences", []):
            lines.append(
                f"- **{d['champ']}** : fiche=`{d.get('valeur_fiche')}` → "
                f"page=`{d.get('valeur_page')}`  \n  > {d.get('extrait_source', '')}"
            )
    if not stale_any:
        lines.append("_Aucun._")

    lines += ["", "## PRs & propositions", ""]
    if pr_results:
        for p in pr_results:
            detail = p.get("pr_url") or p.get("error") or ""
            lines.append(f"- `{p['slug']}` : {p['action']} {detail}")
    else:
        lines.append("_Aucune._")

    return "\n".join(lines) + "\n"


def write_report(markdown: str, reports_dir: Path, timestamp: str) -> Path:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"veille-{timestamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
