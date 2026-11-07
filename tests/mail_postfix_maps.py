#!/usr/bin/python3
"""Check sender normalization with Postfix's actual regexp map engine."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("turnkey_mail", str(ROOT / "bin/turnkey-mail"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mail = importlib.util.module_from_spec(spec)
loader.exec_module(mail)


def query(path, value):
    result = subprocess.run(["postmap", "-q", value, "regexp:" + str(path)], capture_output=True, text=True)
    assert result.returncode in (0, 1), result.stderr
    assert not result.stderr, result.stderr
    return result.stdout.rstrip("\n")


with tempfile.TemporaryDirectory(prefix="turnkey-mail-maps-") as directory:
    envelope, headers = mail.sender_maps("sample.tklapp.com", "noreply@sample.tklapp.com")
    sender = Path(directory) / "sender"
    header = Path(directory) / "header"
    sender.write_text(envelope)
    header.write_text(headers)
    assert query(sender, "wordpress@sample.tklapp.com") == "wordpress@sample.tklapp.com"
    assert query(sender, "root@localhost") == "noreply@sample.tklapp.com"
    assert query(header, "From: WordPress <wordpress@sample.tklapp.com>") == "DUNNO"
    assert query(header, "From: WordPress <admin@example.net>") == "REPLACE From: WordPress <noreply@sample.tklapp.com>"
    assert query(header, "From: root@localhost") == "REPLACE From: noreply@sample.tklapp.com"
    assert query(header, "Sender: Example <admin@example.net>") == "REPLACE Sender: Example <noreply@sample.tklapp.com>"
    for field in ("Reply-To", "To", "Cc", "Bcc"):
        assert query(header, field + ": Person <recipient@example.net>") == ""
print("Postfix sender maps: PASS; owned From/display names preserved, fallback normalized, Reply-To and recipients untouched")
