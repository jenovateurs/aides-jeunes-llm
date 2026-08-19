"""Détection de contenu obsolète : fetch page + comparaison LLM fiche vs page."""
import re
import httpx

from agent.tools.http_client import BROWSER_HEADERS


def html_to_text(html: str) -> str:
    """Supprime scripts/styles/balises, compacte les espaces."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def fetch_page_text(url: str, client: httpx.AsyncClient, max_chars: int = 8000):
    """Renvoie le texte de la page, ou None si injoignable / non-200."""
    try:
        resp = await client.get(url, headers=BROWSER_HEADERS, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return html_to_text(resp.text)[:max_chars]


def coerce_montant(value):
    """Renvoie le montant en nombre, ou None si non exploitable.

    Le champ `montant` d'une fiche est toujours le maximum sous forme numérique :
    on rejette les propositions textuelles ("entre 150 € et 2 280 € selon...").
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.replace(" ", "").replace("\xa0", "")
        cleaned = cleaned.replace(" ", "").replace(",", ".")
        if re.fullmatch(r"\d+(\.\d+)?", cleaned):
            num = float(cleaned)
            return int(num) if num.is_integer() else num
    return None


def filter_divergences(result: dict) -> dict:
    """Anti-hallucination : ignore les divergences sans extrait_source.

    Écarte aussi les propositions de `montant` non numériques (fourchettes,
    barèmes rédigés) que le prompt interdit mais que le LLM produit parfois.
    """
    kept = [
        d for d in result.get("divergences", [])
        if (d.get("extrait_source") or "").strip()
    ]
    proposed = result.get("proposed", {}) or {}
    if "montant" in proposed:
        montant = coerce_montant(proposed["montant"])
        if montant is None:
            proposed.pop("montant")
            kept = [d for d in kept if d.get("champ") != "montant"]
        else:
            proposed["montant"] = montant
    result["divergences"] = kept
    result["proposed"] = proposed
    if not kept or not proposed:
        result["stale"] = False
        result["proposed"] = {}
    return result


async def check_content(dispositif, page_text, llm, prompt) -> dict:
    """Compare la fiche à la page via LLM ; renvoie un résultat filtré."""
    y = dispositif["yaml"]
    user = prompt["user_template"].format(
        label=dispositif.get("label", dispositif["slug"]),
        montant=y.get("montant"),
        conditions=y.get("conditions"),
        description=y.get("description", ""),
        page_text=page_text,
    )
    messages = [
        {"role": "system", "content": prompt["system_prompt"]},
        {"role": "user", "content": user},
    ]
    try:
        raw = await llm.generate_json(messages)
    except Exception as exc:
        return {"slug": dispositif["slug"], "stale": False, "confidence": 0.0,
                "divergences": [], "proposed": {}, "error": str(exc)}
    result = {
        "slug": dispositif["slug"],
        "stale": bool(raw.get("stale")),
        "confidence": float(raw.get("confidence", 0.0)),
        "divergences": raw.get("divergences", []),
        "proposed": raw.get("proposed", {}) or {},
    }
    return filter_divergences(result)
