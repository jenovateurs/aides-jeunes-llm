"""Patch chirurgical d'une fiche YAML (montant/conditions) en préservant le format."""
from pathlib import Path
from ruamel.yaml import YAML

PATCHABLE = ("montant", "conditions")


def patch_benefit_file(path: Path, proposed: dict) -> dict:
    """Applique proposed (montant/conditions) au fichier, format préservé.

    Retourne {"before": {...}, "after": {...}} pour les champs touchés.
    """
    path = Path(path)
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(path.read_text(encoding="utf-8"))

    before, after = {}, {}
    for key in PATCHABLE:
        if key in proposed:
            old = data.get(key)
            # normalise en types Python simples pour le diff
            before[key] = list(old) if hasattr(old, "__iter__") and not isinstance(old, str) else old
            data[key] = proposed[key]
            after[key] = proposed[key]

    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    return {"before": before, "after": after}


def mark_private(path: Path) -> dict:
    """Passe le dispositif en `private: true` (lien cassé), format préservé.

    Retourne {"before": {"private": ...}, "after": {"private": True}}.
    """
    path = Path(path)
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(path.read_text(encoding="utf-8"))
    before = {"private": data.get("private")}
    data["private"] = True
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    return {"before": before, "after": {"private": True}}
