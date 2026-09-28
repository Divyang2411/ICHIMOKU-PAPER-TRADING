"""Encrypted storage of the paper account, so a public repo never shows positions, trades or signals.

The daily GitHub Action decrypts paper.enc -> paper/, trades, and encrypts paper/ -> paper.enc
again. Only paper.enc is committed. The key lives in the PAPER_KEY repository secret and, on your
own computer, in the PAPER_KEY environment variable or the file ~/.ichimoku_paper_key.
"""
import io
import os
import tarfile

from . import config as C

ENC_FILE = os.path.join(C.ROOT, "paper.enc")
KEY_FILE = os.path.expanduser("~/.ichimoku_paper_key")
SKIP = {"report.html"}                       # large and rebuildable: never stored


def new_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def get_key():
    k = os.environ.get("PAPER_KEY", "").strip()
    if not k and os.path.exists(KEY_FILE):
        k = open(KEY_FILE).read().strip()
    if not k:
        raise SystemExit("No key. Set PAPER_KEY or save your key in ~/.ichimoku_paper_key")
    return k.encode()


def lock(paper_dir=None, enc_file=ENC_FILE):
    """paper/ -> paper.enc"""
    from cryptography.fernet import Fernet
    paper_dir = paper_dir or C.PAPER_DIR
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in sorted(os.listdir(paper_dir)):
            p = os.path.join(paper_dir, name)
            if os.path.isfile(p) and name not in SKIP:
                tar.add(p, arcname=name)
    with open(enc_file, "wb") as f:
        f.write(Fernet(get_key()).encrypt(buf.getvalue()))


def unlock(paper_dir=None, enc_file=ENC_FILE):
    """paper.enc -> paper/ (overwrites the files it contains)."""
    from cryptography.fernet import Fernet, InvalidToken
    paper_dir = paper_dir or C.PAPER_DIR
    try:
        raw = Fernet(get_key()).decrypt(open(enc_file, "rb").read())
    except InvalidToken:
        raise SystemExit("Wrong key: it does not match the one used by the GitHub Action (PAPER_KEY secret).")
    os.makedirs(paper_dir, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():
            # flat files only: never follow paths or links out of paper/
            if not m.isfile() or os.path.basename(m.name) != m.name or m.name in ("", ".", ".."):
                continue
            with open(os.path.join(paper_dir, m.name), "wb") as f:
                f.write(tar.extractfile(m).read())
