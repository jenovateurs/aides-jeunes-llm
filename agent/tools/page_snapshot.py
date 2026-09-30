"""Snapshot de page : extraction du contenu principal + normalisation anti-bruit.

Le texte produit est comparé d'un run à l'autre (voir snapshot_diff) : tout ce
qui bouge sans que le dispositif change (date de mise à jour, bandeau cookies,
boutons de partage) doit disparaître ici, sinon chaque run produit un diff.
"""
import re
import unicodedata

import trafilatura

# À incrémenter à chaque changement des règles de `normalize` : un snapshot
# produit par d'autres règles n'est pas comparable (→ nouvelle baseline).
NORMALIZE_VERSION = 1
EXTRACTOR_VERSION = f"trafilatura-{trafilatura.__version__}/norm-{NORMALIZE_VERSION}"

# Lignes de bruit : comparées après `fold`, en début de ligne. Seules les
# lignes courtes sont concernées : « Imprimez le formulaire et envoyez-le avant
# le 30/06 » est du contenu, « Imprimer » est un bouton.
NOISE_PREFIXES = (
    "mis a jour", "derniere mise a jour", "derniere modification",
    "publie le", "modifie le", "©", "copyright", "temps de lecture",
    "partager", "imprimer", "cookies",
)
NOISE_MAX_CHARS = 60

_SPACES = re.compile(r"[ \t\xa0 -   　]+")


def fold(text: str) -> str:
    """Minuscules sans accents, pour des comparaisons tolérantes."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def extract_main_text(html: str) -> str | None:
    """Texte principal de la page (sans nav/footer/bandeaux), ou None si vide."""
    if not html:
        return None
    text = trafilatura.extract(html, include_comments=False,
                               include_tables=True, favor_precision=True)
    return text if text and text.strip() else None


def _is_noise(line: str) -> bool:
    return (len(line) <= NOISE_MAX_CHARS
            and fold(line).startswith(NOISE_PREFIXES))


def normalize(text: str) -> list[str]:
    """Lignes normalisées : espaces compactés, lignes vides et bruit retirés."""
    lines = []
    for raw in text.replace("’", "'").splitlines():
        line = _SPACES.sub(" ", raw).strip()
        if line and not _is_noise(line):
            lines.append(line)
    return lines
