"""Render the free firstboot connection with the real dialog backend."""
import os
from pathlib import Path
import sys
import time
import pexpect
import pyte

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)


def capture(child, screen, stream, name):
    end = time.monotonic() + .4
    while time.monotonic() < end:
        try:
            stream.feed(child.read_nonblocking(65536, timeout=.05))
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass
    text = "\n".join(screen.display)
    (OUT / (name + ".txt")).write_text(text + "\n")
    assert "Can't make new window" not in text
    return text


for cols in (60, 80, 120):
    child = pexpect.spawn("python3", [str(ROOT / "tests/hub_services_terminal_fixture.py")],
                          encoding="utf-8", dimensions=(24, cols), timeout=10,
                          env={**os.environ, "TERM": "linux", "NCURSES_NO_UTF8_ACS": "1"})
    screen = pyte.Screen(cols, 24)
    stream = pyte.Stream(screen)
    text = capture(child, screen, stream, f"{cols}-firstboot-choice")
    assert "Free app email" in text and "Skip" in text
    child.send("\r")
    text = capture(child, screen, stream, f"{cols}-firstboot-approval")
    assert "ABCD&1234" in text and "Check connection" in text
    child.send("\r")
    text = capture(child, screen, stream, f"{cols}-firstboot-connected")
    assert "sample.tklapp.com" in text and "Hub linked" in text
    child.send("\r")
    child.expect("FINISHED")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0
print("Firstboot free connection: PASS at 60/80/120 columns")
