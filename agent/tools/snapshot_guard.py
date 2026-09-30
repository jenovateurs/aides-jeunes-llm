"""Contrôles statiques avant tout envoi à GitHub, et assainissement du texte.

Un diff n'est posté que s'il reflète un vrai changement de la page : pas une
page de blocage anti-bot, pas une redirection vers l'accueil, pas une refonte
complète du site. Chaque contrôle renvoie une raison courte (rapport).
"""
import re
from urllib.parse import urlparse

from agent.tools.page_snapshot import fold

# Pages de blocage / d'erreur servies en 200. Recherchées seulement dans les
# textes courts : une vraie page peut mentionner « captcha » ou « maintenance ».
TRAP_PATTERNS = (
    "captcha", "access denied", "acces refuse", "acces interdit",
    "page introuvable", "page non trouvee", "page not found", "erreur 404",
    "404 not found", "site en maintenance", "en cours de maintenance",
    "javascript est desactive", "activer javascript", "enable javascript",
    "vous n'etes pas un robot", "verify you are human", "just a moment",
)
TRAP_MAX_CHARS = 3000
SIZE_RATIO = (0.5, 2.0)


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is_root(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path in ("", "/") and not parsed.query


def check_page(content_type: str, final_url: str, link: str, text: str,
               min_chars: int) -> list[str]:
    """Raisons de rejet de la page récupérée (vide = page exploitable)."""
    reasons = []
    if "html" not in (content_type or "").lower():
        reasons.append("non_html")
    if len(text) < min_chars:
        reasons.append("texte_court")
    if len(text) < TRAP_MAX_CHARS:
        folded = fold(text.replace("’", "'"))
        if any(p in folded for p in TRAP_PATTERNS):
            reasons.append("page_piege")
    if _host(final_url) != _host(link) or (_is_root(final_url) and not _is_root(link)):
        reasons.append("redirection")
    return reasons


def check_diff(old_chars: int, new_chars: int, churn: float,
               max_churn: float) -> list[str]:
    """Raisons de ne pas envoyer le diff (vide = diff plausible)."""
    reasons = []
    ratio = new_chars / old_chars if old_chars else float("inf")
    if not SIZE_RATIO[0] <= ratio <= SIZE_RATIO[1]:
        reasons.append("taille_anormale")
    if churn > max_churn:
        reasons.append("refonte")
    return reasons


def neutralize_mentions(text: str) -> str:
    """`@login` → `@\\u200blogin` : un texte tiers ne doit notifier personne."""
    return re.sub(r"@(?=[\w-])", "@​", text)


def code_block(text: str, max_chars: int, lang: str = "") -> tuple[str, bool]:
    """Bloc de code sûr, tronqué en fin de ligne à `max_chars` au total.

    La clôture est plus longue que toute suite de backticks du contenu : une
    page tierce ne peut pas refermer le bloc et injecter du Markdown.
    """
    text = neutralize_mentions(text)
    runs = re.findall(r"`+", text)
    fence = "`" * max(3, max((len(r) for r in runs), default=0) + 1)
    overhead = 2 * len(fence) + len(lang) + 2
    budget = max_chars - overhead
    truncated = len(text) > budget
    if truncated:
        text = text[:budget]
        text = text[:text.rfind("\n")] if "\n" in text else ""
    return f"{fence}{lang}\n{text}\n{fence}", truncated
