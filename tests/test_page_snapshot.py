"""Tests extraction + normalisation des snapshots de page."""
from agent.tools.page_snapshot import (EXTRACTOR_VERSION, extract_main_text,
                                       fold, normalize)

HTML = """<html><head><title>Aide</title><style>.x{}</style></head><body>
<nav><ul><li>Accueil</li><li>Nos aides</li><li>Contact</li></ul></nav>
<header>Bandeau du site — menu principal</header>
<main><article>
<h1>Aide au permis de conduire</h1>
<p>Cette aide de 500 € est destinée aux jeunes de 18 à 25 ans résidant dans le
département. Elle finance une partie de la formation au permis B.</p>
<p>Les demandes sont à déposer avant le 30/06/2026 auprès du service jeunesse,
accompagnées d'un justificatif de domicile et d'une pièce d'identité.</p>
</article></main>
<footer>Mentions légales — Plan du site — © Département 2026</footer>
</body></html>"""


def test_extract_keeps_main_content_and_drops_nav_footer():
    text = extract_main_text(HTML)
    assert "500 €" in text
    assert "30/06/2026" in text
    assert "Nos aides" not in text
    assert "Mentions légales" not in text


def test_extract_returns_none_on_empty():
    assert extract_main_text("") is None
    assert extract_main_text("<html><body></body></html>") is None


def test_normalize_spaces_and_empty_lines():
    text = "  Montant :\xa01 000   €  \n\n\t\nL’aide  est versée "
    assert normalize(text) == ["Montant : 1 000 €", "L'aide est versée"]


def test_normalize_drops_short_noise_lines():
    text = "\n".join([
        "Mis à jour le 12/09/2026",
        "Dernière modification : 3 septembre 2026",
        "Publié le 01/01/2026",
        "© Région 2026",
        "Partager la page",
        "IMPRIMER",
        "Temps de lecture : 3 minutes",
        "Contenu utile",
    ])
    assert normalize(text) == ["Contenu utile"]


def test_normalize_keeps_real_sentences_starting_with_noise_word():
    phrase = ("Imprimez le formulaire et envoyez-le avant le 30/06/2026 "
              "au service jeunesse du département")
    assert normalize(phrase) == [phrase]


def test_normalize_keeps_noise_word_not_at_start():
    assert normalize("Vous pouvez partager") == ["Vous pouvez partager"]


def test_fold_strips_accents_and_case():
    assert fold("Clôturé ÉTÉ") == "cloture ete"


def test_extractor_version_names_trafilatura():
    assert EXTRACTOR_VERSION.startswith("trafilatura-")
