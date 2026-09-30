"""Tests du commentaire d'en-tête `# veille-snapshot`."""
from agent.tools.yaml_updater import add_header_comment

SOURCE = "label: Aide  # commentaire inline\nmontant: 500\nconditions:\n  - a\n"


def test_add_header_comment_keeps_file_intact(tmp_path):
    path = tmp_path / "a.yml"
    path.write_text(SOURCE, encoding="utf-8")
    add_header_comment(path, "veille-snapshot 2026-09-30 : page modifiée")
    assert path.read_text(encoding="utf-8") == (
        "# veille-snapshot 2026-09-30 : page modifiée\n" + SOURCE)


def test_add_header_comment_replaces_previous(tmp_path):
    path = tmp_path / "a.yml"
    path.write_text("# veille-snapshot ancien\n" + SOURCE, encoding="utf-8")
    add_header_comment(path, "veille-snapshot nouveau")
    assert path.read_text(encoding="utf-8") == "# veille-snapshot nouveau\n" + SOURCE


def test_add_header_comment_keeps_other_header_comments(tmp_path):
    path = tmp_path / "a.yml"
    path.write_text("# note humaine\n" + SOURCE, encoding="utf-8")
    add_header_comment(path, "veille-snapshot x")
    assert path.read_text(encoding="utf-8") == (
        "# veille-snapshot x\n# note humaine\n" + SOURCE)
