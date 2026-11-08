"""Firstboot dialog exercise with fixture Hub responses, no system writes."""
import importlib.util
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.basicConfig(stream=sys.stderr, level=logging.CRITICAL)
spec = importlib.util.spec_from_file_location("hubservices", ROOT / "bin/hubservices.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Dialog = module.Dialog


def dialog(*args, **kwargs):
    result = Dialog(*args, **kwargs)
    result.console.add_persistent_args(["--ascii-lines"])
    return result


module.Dialog = dialog
responses = iter([
    {"verification_uri": "https://hub.turnkeylinux.org/link", "user_code": "D34C-F5A9"},
    {"status": "ready", "domain": "sample.tklapp.com"},
])
module.mail_call = lambda *args: next(responses)
sys.argv = ["hubservices.py"]
module.main()
print("FINISHED")
