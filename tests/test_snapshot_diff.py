"""Tests diff de snapshots + détection des déclencheurs (dates, montants, clôture)."""
import pytest

from agent.tools.snapshot_diff import (compute_diff, extract_amounts,
                                       extract_dates, strong_cloture)

TODAY = "2026-09-30"


@pytest.mark.parametrize("text,expected", [
    ("avant le 30/06/2026", {"2026-06-30"}),
    ("avant le 30-06-2026", {"2026-06-30"}),
    ("avant le 30.06.2026", {"2026-06-30"}),
    ("dès le 1er septembre 2026", {"2026-09-01"}),
    ("le 1 septembre 2026", {"2026-09-01"}),
    ("le 01/09/2026", {"2026-09-01"}),
    ("jusqu'au 15 août", {"--08-15"}),
    ("à partir de février 2027", {"2027-02"}),
    ("pour l'année scolaire 2026-2027", {"2026/2027"}),
    ("pour l'année 2026/2027", {"2026/2027"}),
    ("les années 2020-2030", set()),
    ("téléphone 01 23 45 67 89", set()),
    ("du 1er juillet 2026 au 31/12/2026", {"2026-07-01", "2026-12-31"}),
])
def test_extract_dates(text, expected):
    assert extract_dates(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("une aide de 500 €", {"500"}),
    ("une aide de 500€", {"500"}),
    ("1 000 € maximum", {"1000"}),
    ("1000 euros", {"1000"}),
    ("1.000 EUR", {"1000"}),
    ("12 345,50 €", {"12345,50"}),
    ("150,00 €", {"150"}),
    ("1 euro symbolique", {"1"}),
    ("de 150 € à 2 280 €", {"150", "2280"}),
    ("500 personnes", set()),
])
def test_extract_amounts(text, expected):
    assert extract_amounts(text) == expected


def test_identical_short_circuits():
    res = compute_diff(["a", "b"], ["a", "b"], TODAY)
    assert res["changed"] is False
    assert res["triggers"] == {}
    assert res["diff"] == ""


def test_date_change_triggers():
    res = compute_diff(["Dépôt avant le 30/06/2026."],
                       ["Dépôt avant le 31/07/2026."], TODAY)
    assert res["changed"] is True
    assert res["triggers"]["dates"] == (["2026-06-30"], ["2026-07-31"])
    assert "-Dépôt avant le 30/06/2026." in res["diff"]
    assert "+Dépôt avant le 31/07/2026." in res["diff"]


def test_amount_change_triggers():
    res = compute_diff(["Aide de 500 €"], ["Aide de 600 €"], TODAY)
    assert res["triggers"] == {"montants": (["500"], ["600"])}


def test_rewording_with_same_values_does_not_trigger():
    res = compute_diff(["Aide de 1 000 € avant le 30/06/2026."],
                       ["Avant le 30 juin 2026, demandez 1000 euros."], TODAY)
    assert res["changed"] is True
    assert res["triggers"] == {}


def test_moved_or_duplicated_line_does_not_trigger():
    old = ["Intro", "Montant : 500 €", "Fin"]
    new = ["Montant : 500 €", "Intro", "Fin", "Rappel : 500 €"]
    assert compute_diff(old, new, TODAY)["triggers"] == {}


def test_cloture_only_on_added_lines():
    removed = compute_diff(["Le dispositif est fermé.", "Texte"], ["Texte"], TODAY)
    assert "cloture" not in removed["triggers"]
    added = compute_diff(["Texte"], ["Texte", "Inscriptions closes."], TODAY)
    assert added["triggers"]["cloture"] == ["Inscriptions closes."]


def test_cloture_already_present_and_reworded_does_not_trigger():
    res = compute_diff(["La fermeture est prévue en fin d'année."],
                       ["La fermeture est prévue à la fin de l'année."], TODAY)
    assert "cloture" not in res["triggers"]


@pytest.mark.parametrize("word", ["clôturé", "clôture", "cloture", "clos",
                                  "closes", "fermé", "fermées", "fermeture"])
def test_cloture_words(word):
    res = compute_diff(["x"], ["x", f"Le guichet {word} ici"], TODAY)
    assert res["triggers"].get("cloture")


@pytest.mark.parametrize("line", ["La ferme pédagogique", "Fermer la fenêtre",
                                  "Un enclos", "Clostridium"])
def test_cloture_words_not_matched(line):
    assert "cloture" not in compute_diff(["x"], ["x", line], TODAY)["triggers"]


@pytest.mark.parametrize("line", [
    "Le dispositif est clôturé.",
    "Les candidatures sont désormais closes.",
    "Cette aide n'est plus disponible.",
    "L’aide n’est plus proposée.",
    "Le programme a pris fin le 30/06/2026.",
    "Ce dispositif n'existe plus.",
    "Les inscriptions pour 2026 sont closes.",
])
def test_strong_cloture(line):
    assert strong_cloture(line, TODAY)


@pytest.mark.parametrize("line", [
    "Date de clôture des candidatures : 30/06/2027",
    "Les candidatures sont closes le 15 octobre",
    "Les demandes sont closes le 31/12/2026.",
    "Accueil fermé le dimanche",
    "Fermeture estivale du service",
])
def test_not_strong_cloture(line):
    assert not strong_cloture(line, TODAY)


def test_strong_cloture_reported_in_result():
    res = compute_diff(["Aide ouverte."], ["Le dispositif est clos."], TODAY)
    assert res["strong_cloture"] == ["Le dispositif est clos."]
    weak = compute_diff(["x"], ["x", "Accueil fermé le dimanche"], TODAY)
    assert weak["strong_cloture"] == []
    assert weak["triggers"]["cloture"]


def test_churn_ratio():
    assert compute_diff(["a", "b", "c", "d"], ["a", "b", "c", "X"], TODAY)["churn"] == 0.25
    assert compute_diff(["a", "b"], ["c", "d"], TODAY)["churn"] == 1.0
