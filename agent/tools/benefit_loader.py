"""Parsing des dispositifs (benefits) pour l'agent Veille."""
import json
from pathlib import Path
import yaml

LINK_FIELDS = ["link", "instructions", "form", "teleservice"]


def extract_links(benefit: dict) -> list[dict]:
    """Retourne les liens dédupliqués du dispositif : [{url, type}].

    Même URL portée par plusieurs champs → types joints par ' / '.
    Ignore les valeurs non-string.
    """
    by_url: dict[str, list[str]] = {}
    for field in LINK_FIELDS:
        value = benefit.get(field)
        if isinstance(value, str) and value.strip():
            by_url.setdefault(value, []).append(field)
    return [{"url": url, "type": " / ".join(types)} for url, types in by_url.items()]


def load_dispositifs(benefits_dir, only_private: bool = False) -> list[dict]:
    """Charge les dispositifs depuis un ou plusieurs dossiers de *.yml.

    `benefits_dir` accepte un chemin unique ou une liste (ex. `javascript/` +
    `openfisca/`). slug = nom de fichier (sans extension). Chaque dispositif
    porte son `path` (fichier source) et `dir` (nom du dossier), nécessaires
    pour patcher le bon fichier lors d'une PR.

    Par défaut seules les fiches publiques sont retournées. `only_private`
    inverse le filtre : c'est le mode revival, qui reteste les fiches déjà
    passées en `private` pour voir si leurs liens revivent.
    """
    dirs = ([benefits_dir] if isinstance(benefits_dir, (str, Path))
            else list(benefits_dir))
    dispositifs: list[dict] = []
    for path in sorted(p for d in dirs for p in Path(d).glob("*.yml")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if bool(data.get("private")) is not only_private:
            continue
        dispositifs.append({
            "slug": path.stem,
            "path": path,
            "dir": path.parent.name,
            "label": data.get("label", path.stem),
            "institution": data.get("institution", ""),
            "links": extract_links(data),
            "yaml": data,
        })
    return dispositifs


def load_covoiturage(json_path: Path) -> list[dict]:
    """Charge les incitations covoiturage (`dynamic/incitations-covoiturage.json`).

    Ces aides ne sont pas des fiches YAML : elles vivent dans un tableau JSON,
    sans slug de fichier ni champ `private`. Elles sont donc marquées
    `check_only` — l'agent vérifie leurs liens mais n'ouvre jamais de PR.
    slug = `covoiturage-<code_siren>` (index en secours si le siren manque).
    """
    try:
        entries = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(entries, list):
        return []
    dispositifs: list[dict] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        links = extract_links(entry)
        if not links:
            continue
        siren = str(entry.get("code_siren") or "").strip() or f"index-{i}"
        operateurs = entry.get("operateurs") or entry.get("nom_plateforme") or ""
        dispositifs.append({
            "slug": f"covoiturage-{siren}",
            "path": Path(json_path),
            "dir": "dynamic",
            "check_only": True,
            "label": f"Incitation covoiturage {siren}"
                     + (f" ({operateurs})" if operateurs else ""),
            "institution": "",
            "links": links,
            "yaml": entry,
        })
    return dispositifs
