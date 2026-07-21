"""Parsing des dispositifs (benefits) pour l'agent Veille."""
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


def load_dispositifs(benefits_dir: Path) -> list[dict]:
    """Charge les dispositifs publics depuis un dossier de *.yml.

    slug = nom de fichier (sans extension). Ignore les fiches `private`.
    """
    dispositifs: list[dict] = []
    for path in sorted(Path(benefits_dir).glob("*.yml")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("private"):
            continue
        dispositifs.append({
            "slug": path.stem,
            "label": data.get("label", path.stem),
            "institution": data.get("institution", ""),
            "links": extract_links(data),
            "yaml": data,
        })
    return dispositifs
