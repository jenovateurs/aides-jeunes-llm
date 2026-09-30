"""Rendu de la veille snapshots : rapport local, body et commentaire de PR.

Tout texte venu de la page (lignes de clôture, diff) passe par `code_block` :
bloc de code à clôture sûre, mentions neutralisées, troncature en fin de ligne.
Les valeurs de déclencheurs sont déjà normalisées (chiffres, dates ISO).
"""
from agent.tools.snapshot_guard import code_block

TRIGGER_LABELS = {"dates": "dates", "montants": "montants", "cloture": "clôture ?"}
TRUNCATED_NOTE = "\n\n> Diff tronqué, voir le snapshot local."


def iso(day: str) -> str:
    """YYYYMMDD → YYYY-MM-DD."""
    return f"{day[:4]}-{day[4:6]}-{day[6:]}" if day and len(day) == 8 else day or ""


def trigger_labels(triggers: dict) -> str:
    return ", ".join(TRIGGER_LABELS[k] for k in TRIGGER_LABELS if k in triggers)


def _values(values) -> str:
    return ", ".join(f"`{v}`" for v in values) or "—"


def trigger_summary(triggers: dict) -> list[str]:
    lines = []
    if "dates" in triggers:
        before, after = triggers["dates"]
        lines.append(f"- Dates : {_values(before)} → {_values(after)}")
    if "montants" in triggers:
        before, after = triggers["montants"]
        lines.append(f"- Montants (€) : {_values(before)} → {_values(after)}")
    if "cloture" in triggers:
        lines.append(f"- Clôture : {len(triggers['cloture'])} ligne(s) ajoutée(s)")
    return lines


def pr_title(disp: dict, result: dict) -> str:
    label = disp.get("label", disp["slug"])
    if result["strong_cloture"]:
        return f"veille: {label} — clôture détectée, passage en private"
    return f"veille: {label} — page modifiée ({trigger_labels(result['triggers'])})"


def header_comment(result: dict, today_iso: str) -> str:
    return (f"veille-snapshot {today_iso} : page modifiée "
            f"({trigger_labels(result['triggers'])}) — à vérifier")


def pr_body(disp: dict, prev_day: str, day: str, result: dict, yaml_diff) -> str:
    """Body de la nouvelle PR. `yaml_diff` = retour de mark_private, ou None
    si seul le commentaire d'en-tête a été posé."""
    lines = [f"## Dispositif : {disp.get('label', disp['slug'])}",
             f"- Institution : {disp.get('institution', '')}",
             f"- Lien source : {disp['yaml'].get('link', '')}",
             f"- Snapshots comparés : {iso(prev_day)} → {iso(day)}",
             "", "### Déclencheurs", ""]
    lines += trigger_summary(result["triggers"])
    if result["triggers"].get("cloture"):
        block, _ = code_block("\n".join(result["triggers"]["cloture"]), 4000)
        lines += ["", "Lignes de clôture ajoutées :", "", block]
    lines += ["", "### Action proposée"]
    if yaml_diff is not None:
        lines.append(f"- `private` : `{yaml_diff['before'].get('private')}` → "
                     "`true` (tournure de clôture explicite)")
    else:
        lines.append("- Commentaire `# veille-snapshot` ajouté en tête de fiche : "
                     "la fiche est **à vérifier** contre la page.")
    lines += ["", "Le diff complet de la page est posté en commentaire.", "",
              "> PR générée automatiquement par la veille snapshots (aj-llm) — "
              "corriger la fiche sur cette branche puis retirer le commentaire "
              "`# veille-snapshot` avant merge."]
    return "\n".join(lines)


def pr_comment(prev_day: str, day: str, result: dict, max_chars: int) -> str:
    """Commentaire de PR : déclencheurs + diff, au plus `max_chars` caractères."""
    head = "\n".join([f"### Snapshot du {iso(day)} vs {iso(prev_day)}", "",
                      *trigger_summary(result["triggers"]), "", ""])
    budget = max_chars - len(head) - len(TRUNCATED_NOTE)
    block, truncated = code_block(result["diff"], budget, lang="diff")
    return head + block + (TRUNCATED_NOTE if truncated else "")


# ── Rapport local ──────────────────────────────────────────────────────


def _line(r: dict) -> str:
    out = f"- `{r['slug']}` {r.get('label', '')}"
    if r.get("triggers"):
        out += f" — {trigger_labels(r['triggers'])}"
        if r.get("strong_cloture"):
            out += " (clôture forte → private)"
    if r.get("action"):
        out += f" — **{r['action']}**"
    if r.get("pr_url"):
        out += f" {r['pr_url']}"
    if r.get("reasons"):
        out += f" — {', '.join(r['reasons'])}"
    if r.get("error"):
        out += f" — {r['error']}"
    return out


def render_report(results: list[dict], generated_at: str, dry_run: bool,
                  pr_mode: str) -> str:
    triggered = [r for r in results if r["status"] == "triggered"]
    sections = [
        ("Clôtures détectées",
         [r for r in triggered if "cloture" in r["triggers"]]),
        ("Dates/montants modifiés",
         [r for r in triggered if "cloture" not in r["triggers"]]),
        ("Suspects (non envoyés)", [r for r in results if r["status"] == "suspect"]),
        ("Bloquées (PR refusée)", [r for r in results if r.get("action") == "blocked"]),
        ("Erreurs PR", [r for r in results
                        if r.get("action") in ("pr_error", "invalid_patch")]),
        ("Changements sans déclencheur",
         [r for r in results if r["status"] == "changed"]),
        ("Échecs fetch/extraction", [r for r in results
                                     if r["status"] in ("fetch_error", "extract_error")]),
        ("Nouvelles baselines", [r for r in results if r["status"] == "baseline"]),
    ]
    mode = "dry-run (rien n'est écrit ni envoyé)" if dry_run else f"PR : {pr_mode}"
    lines = [f"# Veille snapshots — {generated_at}", "", f"Mode : {mode}"]
    for title, rows in sections:
        lines += ["", f"## {title} ({len(rows)})", ""]
        lines += [_line(r) for r in rows] or ["_Aucun._"]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines += ["", "## Résumé", "", f"- Fiches traitées : {len(results)}"]
    lines += [f"- {status} : {n}" for status, n in sorted(counts.items())]
    return "\n".join(lines) + "\n"
