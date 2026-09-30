"""Tests contrôles statiques avant envoi + assainissement du texte GitHub."""
import pytest

from agent.tools.snapshot_guard import (check_diff, check_page, code_block,
                                        neutralize_mentions)

LINK = "https://www.exemple.fr/aides/permis"
TEXT = "Contenu utile du dispositif. " * 20


def test_page_ok():
    assert check_page("text/html; charset=utf-8", LINK, LINK, TEXT, min_chars=300) == []


def test_page_not_html():
    assert "non_html" in check_page("application/pdf", LINK, LINK, TEXT, 300)


def test_page_too_short():
    assert "texte_court" in check_page("text/html", LINK, LINK, "court", 300)


@pytest.mark.parametrize("trap", [
    "Please complete the CAPTCHA", "Access denied", "Accès refusé",
    "Page introuvable", "Erreur 404", "Site en maintenance",
    "Veuillez activer JavaScript", "Just a moment...",
])
def test_page_trap(trap):
    text = f"{trap}. " + "x " * 200
    assert "page_piege" in check_page("text/html", LINK, LINK, text, 300)


def test_trap_words_in_long_page_are_content():
    text = "Formulaire protégé par captcha. " + "Contenu utile. " * 300
    assert check_page("text/html", LINK, LINK, text, 300) == []


def test_redirect_other_domain():
    assert "redirection" in check_page("text/html", "https://autre.fr/aides", LINK, TEXT, 300)


def test_redirect_www_is_same_domain():
    assert check_page("text/html", "https://exemple.fr/aides/permis", LINK, TEXT, 300) == []


def test_redirect_to_root():
    assert "redirection" in check_page("text/html", "https://www.exemple.fr/", LINK, TEXT, 300)
    root = "https://www.exemple.fr/"
    assert check_page("text/html", root, root, TEXT, 300) == []


def test_diff_ok():
    assert check_diff(1000, 1100, churn=0.1, max_churn=0.5) == []


@pytest.mark.parametrize("old,new", [(1000, 400), (1000, 2500)])
def test_diff_size_ratio(old, new):
    assert "taille_anormale" in check_diff(old, new, churn=0.1, max_churn=0.5)


def test_diff_refonte():
    assert "refonte" in check_diff(1000, 1000, churn=0.6, max_churn=0.5)


def test_neutralize_mentions():
    out = neutralize_mentions("contact @admin ou mail a@b.fr")
    assert "@admin" not in out
    assert "@​admin" in out


def test_code_block_fence_longer_than_content_backticks():
    block, truncated = code_block("a ```` b", 1000, lang="diff")
    assert block.startswith("`````diff\n")
    assert block.endswith("\n`````")
    assert truncated is False


def test_code_block_truncates_on_line_boundary():
    text = "\n".join(f"ligne {i:03d}" for i in range(100))
    block, truncated = code_block(text, 200, lang="diff")
    assert truncated is True
    assert len(block) <= 200
    body = block.split("\n")[1:-1]
    assert all(l.startswith("ligne ") and len(l) == 9 for l in body)
