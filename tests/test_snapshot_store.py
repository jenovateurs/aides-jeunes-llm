"""Tests stockage local des snapshots."""
import json

from agent.tools.snapshot_store import SnapshotStore, sha256_lines


def _store(tmp_path, keep=8):
    return SnapshotStore(tmp_path / "snaps", keep=keep)


def test_save_then_latest_before(tmp_path):
    store = _store(tmp_path)
    store.save("a", "20260901", ["l1", "l2"], url="https://x", extractor="e1")
    snap = store.latest_before("a", "20260902")
    assert snap == {"date": "20260901", "lines": ["l1", "l2"],
                    "sha256": sha256_lines(["l1", "l2"])}
    assert store.latest_before("a", "20260901") is None
    assert store.latest_before("inconnu", "20260902") is None


def test_meta_records_url_extractor_and_snapshots(tmp_path):
    store = _store(tmp_path)
    store.save("a", "20260901", ["l1"], url="https://x", extractor="e1")
    meta = store.load_meta("a")
    assert meta["url"] == "https://x"
    assert meta["extractor"] == "e1"
    assert meta["snapshots"] == [
        {"date": "20260901", "sha256": sha256_lines(["l1"]), "chars": 2}]


def test_same_day_overwrites(tmp_path):
    store = _store(tmp_path)
    store.save("a", "20260901", ["v1"], url="u", extractor="e")
    store.save("a", "20260901", ["v2"], url="u", extractor="e")
    meta = store.load_meta("a")
    assert [s["date"] for s in meta["snapshots"]] == ["20260901"]
    assert store.latest_before("a", "20260902")["lines"] == ["v2"]


def test_identical_content_is_not_duplicated(tmp_path):
    store = _store(tmp_path)
    store.save("a", "20260901", ["v1"], url="u", extractor="e")
    store.save("a", "20260908", ["v1"], url="u", extractor="e")
    assert [s["date"] for s in store.load_meta("a")["snapshots"]] == ["20260901"]
    assert not (tmp_path / "snaps" / "a" / "20260908.txt").exists()


def test_purge_keeps_last_n(tmp_path):
    store = _store(tmp_path, keep=3)
    for day in range(1, 6):
        store.save("a", f"2026090{day}", [f"v{day}"], url="u", extractor="e")
    dates = [s["date"] for s in store.load_meta("a")["snapshots"]]
    assert dates == ["20260903", "20260904", "20260905"]
    files = sorted(p.name for p in (tmp_path / "snaps" / "a").glob("*.txt"))
    assert files == ["20260903.txt", "20260904.txt", "20260905.txt"]


def test_url_or_extractor_change_is_recorded(tmp_path):
    store = _store(tmp_path)
    store.save("a", "20260901", ["v1"], url="u1", extractor="e1")
    store.save("a", "20260902", ["v2"], url="u2", extractor="e2")
    meta = store.load_meta("a")
    assert (meta["url"], meta["extractor"]) == ("u2", "e2")


def test_last_posted_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.save("a", "20260901", ["v1"], url="u", extractor="e")
    store.set_last_posted("a", "https://pr/1", "abc")
    assert store.load_meta("a")["last_posted"] == {"pr": "https://pr/1", "sha": "abc"}
    # conservé par un save ultérieur
    store.save("a", "20260902", ["v2"], url="u", extractor="e")
    assert store.load_meta("a")["last_posted"]["sha"] == "abc"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    store = _store(tmp_path)
    store.save("a", "20260901", ["v1"], url="u", extractor="e")
    names = {p.name for p in (tmp_path / "snaps" / "a").iterdir()}
    assert names == {"20260901.txt", "meta.json"}
    json.loads((tmp_path / "snaps" / "a" / "meta.json").read_text())
