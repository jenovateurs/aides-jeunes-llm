"""Diff entre deux snapshots et détection déterministe des déclencheurs.

On compare des **ensembles de valeurs normalisées** (dates, montants) entre
lignes retirées et ajoutées, pas les lignes elles-mêmes : une phrase
reformulée, déplacée ou dédoublée qui garde les mêmes valeurs ne déclenche
rien. « 1 000 € » et « 1000 euros » sont la même valeur, « 01/09/2026 » et
« 1er septembre 2026 » aussi.
"""
import difflib
import re

from agent.tools.page_snapshot import fold

MONTHS = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
          "aout", "septembre", "octobre", "novembre", "decembre")
_MONTH = "|".join(MONTHS)

# Motifs appliqués sur le texte `fold`é, dans cet ordre : chaque date trouvée
# est retirée du texte pour ne pas être recomptée par un motif plus lâche
# (« 1er septembre 2026 » ne doit pas aussi donner « septembre 2026 »).
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_DAY_MONTH = re.compile(rf"\b(1er|\d{{1,2}})\s+({_MONTH})(?:\s+(\d{{4}}))?\b")
_MONTH_YEAR = re.compile(rf"\b({_MONTH})\s+(\d{{4}})\b")
_SCHOOL_YEAR = re.compile(r"\b(\d{4})\s?[-/]\s?(\d{4})\b")

_AMOUNT = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[ .]\d{3})+|\d+)(?:[,.](\d{1,2}))?\s?(?:€|euros?\b|eur\b)")

# Mot de clôture (ligne en minuscules, accents conservés : « fermé » ≠ « ferme »).
_CLOTURE_WORD = re.compile(
    r"\b(?:cl[oô]tur\w*|clos|close|closes|fermée?s?|fermeture)\b")

# Clôture forte : tournure explicite de fin du dispositif (texte `fold`é).
_SUBJECT = (r"(?:dispositif|aide|appel a (?:projets?|candidatures?)|programme|"
            r"operation|candidatures?|inscriptions?|demandes?|depots?)")
_STATE = r"(?:clos|close|closes|clotures?|cloturees?|fermes?|fermees?|termines?|terminees?)"
_STRONG = (
    re.compile(rf"\b{_SUBJECT}\s+(?:[\w'-]+\s+){{0,4}}?(?:est|sont)\s+"
               rf"(?:desormais\s+|definitivement\s+|actuellement\s+)?{_STATE}\b"),
    re.compile(r"\bn'est plus (?:disponible|accessible|en vigueur|propose|ouvert)"),
    re.compile(r"\b(?:a|ont) pris fin\b"),
    re.compile(r"\bn'existe plus\b"),
)


def _prep(text: str) -> str:
    return fold(text.replace("’", "'"))


def extract_dates(text: str) -> set[str]:
    """Dates normalisées : AAAA-MM-JJ, --MM-JJ (sans année), AAAA-MM, AAAA/AAAA."""
    text = _prep(text)
    found: set[str] = set()

    def numeric(m):
        d, mo, y = int(m[1]), int(m[2]), m[3]
        if 1 <= d <= 31 and 1 <= mo <= 12:
            found.add(f"{y}-{mo:02d}-{d:02d}")
        return " "

    def day_month(m):
        d = 1 if m[1] == "1er" else int(m[1])
        if not 1 <= d <= 31:
            return m[0]
        mo = MONTHS.index(m[2]) + 1
        found.add(f"{m[3]}-{mo:02d}-{d:02d}" if m[3] else f"--{mo:02d}-{d:02d}")
        return " "

    def month_year(m):
        found.add(f"{m[2]}-{MONTHS.index(m[1]) + 1:02d}")
        return " "

    text = _NUMERIC_DATE.sub(numeric, text)
    text = _DAY_MONTH.sub(day_month, text)
    text = _MONTH_YEAR.sub(month_year, text)
    for a, b in _SCHOOL_YEAR.findall(text):
        if int(b) == int(a) + 1:
            found.add(f"{a}/{b}")
    return found


def extract_amounts(text: str) -> set[str]:
    """Montants normalisés : entier sans séparateur, décimales si non nulles."""
    found = set()
    for integer, decimals in _AMOUNT.findall(_prep(text)):
        value = str(int(re.sub(r"[ .]", "", integer)))
        if decimals and int(decimals):
            value += "," + decimals.ljust(2, "0")
        found.add(value)
    return found


def _is_past(value: str, today: str) -> bool:
    """Vrai si la date normalisée est passée. Date sans année = non passée."""
    if value.startswith("--"):
        return False
    if "/" in value:  # année scolaire : se termine fin août de la 2e année
        return f"{value[5:]}-08-31" < today
    if len(value) == 7:  # AAAA-MM
        return value < today[:7]
    return value < today


def strong_cloture(line: str, today: str) -> bool:
    """Tournure explicite de fin du dispositif, sans date à venir dans la ligne.

    « Date de clôture des candidatures : 30/06/2027 » annonce une échéance, pas
    une fin : une date non passée dans la ligne annule la clôture forte.
    """
    text = _prep(line)
    if not any(p.search(text) for p in _STRONG):
        return False
    return all(_is_past(d, today) for d in extract_dates(line))


def _values(lines, extract) -> set[str]:
    return set().union(*(extract(l) for l in lines)) if lines else set()


def compute_diff(old: list[str], new: list[str], today: str) -> dict:
    """Diff unifié + déclencheurs. `today` = AAAA-MM-JJ (clôture forte)."""
    if old == new:
        return {"changed": False, "triggers": {}, "strong_cloture": [],
                "churn": 0.0, "diff": ""}
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            a=old, b=new, autojunk=False).get_opcodes():
        if tag != "equal":
            removed += old[i1:i2]
            added += new[j1:j2]

    triggers: dict = {}
    for name, extract in (("dates", extract_dates), ("montants", extract_amounts)):
        before, after = _values(removed, extract), _values(added, extract)
        if before != after:
            triggers[name] = (sorted(before - after), sorted(after - before))

    # Clôture : seulement si les lignes ajoutées en portent plus que les
    # retirées (une phrase de clôture déjà présente et reformulée ne compte pas).
    def cloture_lines(lines):
        return [l for l in lines if _CLOTURE_WORD.search(l.lower())]
    added_cl, removed_cl = cloture_lines(added), cloture_lines(removed)
    strong = []
    if len(added_cl) > len(removed_cl):
        triggers["cloture"] = added_cl
        strong = [l for l in added_cl if strong_cloture(l, today)]

    total = len(old) + len(new)
    diff = "\n".join(difflib.unified_diff(
        old, new, fromfile="avant", tofile="après", n=2, lineterm=""))
    return {"changed": True, "triggers": triggers, "strong_cloture": strong,
            "churn": (len(removed) + len(added)) / total if total else 0.0,
            "diff": diff}
