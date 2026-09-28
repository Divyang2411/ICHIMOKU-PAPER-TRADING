import io
import tarfile

import pytest
from cryptography.fernet import Fernet

from ichimoku import vault


@pytest.fixture
def key(monkeypatch):
    k = vault.new_key()
    monkeypatch.setenv("PAPER_KEY", k)
    return k


def test_roundtrip_and_ciphertext_hides_content(tmp_path, key):
    src, dst, enc = tmp_path / "paper", tmp_path / "out", tmp_path / "paper.enc"
    src.mkdir()
    (src / "state.json").write_text('{"positions": {"RELIANCE": 1}}')
    (src / "positions.csv").write_text("symbol\nRELIANCE\n")
    (src / "report.html").write_text("big")
    vault.lock(str(src), str(enc))
    assert b"RELIANCE" not in enc.read_bytes()
    vault.unlock(str(dst), str(enc))
    assert (dst / "state.json").read_text() == '{"positions": {"RELIANCE": 1}}'
    assert (dst / "positions.csv").exists() and not (dst / "report.html").exists()


def test_wrong_key_is_rejected(tmp_path, key, monkeypatch):
    src = tmp_path / "paper"; src.mkdir(); (src / "a.csv").write_text("x")
    vault.lock(str(src), str(tmp_path / "e"))
    monkeypatch.setenv("PAPER_KEY", vault.new_key())
    with pytest.raises(SystemExit, match="Wrong key"):
        vault.unlock(str(tmp_path / "o"), str(tmp_path / "e"))


def test_unlock_ignores_paths_outside_paper_dir(tmp_path, key):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name in ("../evil.txt", "sub/x.txt", "ok.csv"):
            data = b"x"
            ti = tarfile.TarInfo(name); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
    (tmp_path / "e").write_bytes(Fernet(key.encode()).encrypt(buf.getvalue()))
    out = tmp_path / "paper"
    vault.unlock(str(out), str(tmp_path / "e"))
    assert sorted(p.name for p in out.iterdir()) == ["ok.csv"]
    assert not (tmp_path / "evil.txt").exists()


def test_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_KEY", raising=False)
    monkeypatch.setattr(vault, "KEY_FILE", str(tmp_path / "nokey"))
    with pytest.raises(SystemExit, match="No key"):
        vault.get_key()
