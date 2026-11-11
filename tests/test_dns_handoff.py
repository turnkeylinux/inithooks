import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock

loader = importlib.machinery.SourceFileLoader("dns_handoff_helper", str(Path(__file__).resolve().parents[1] / "bin/turnkey-hub"))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)

class DNSHandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(helper, "HUBDNS_DIR", Path(self.temp.name));p.start();self.addCleanup(p.stop)
        self.result = {"connection_status": "linked", "services": {"dns": {"enabled": True}}}
    def response(self, rc=0, text="1.5.0"):
        return types.SimpleNamespace(returncode=rc, stdout=text)
    def test_compatible_service_owns_initialization(self):
        with patch.object(helper.subprocess, "run", side_effect=[self.response(), self.response(), self.response()]) as run:
            helper.initialize_linked_dns(self.result)
        self.assertEqual(["/usr/bin/hubdns-init"], run.call_args.args[0])
        self.assertEqual(helper.subprocess.DEVNULL, run.call_args.kwargs["stdin"])
        self.assertNotIn("dns_setup_error", self.result)
    def test_existing_legacy_state_is_a_local_noop(self):
        (helper.HUBDNS_DIR / "sub_apikey").write_text("legacy")
        with patch.object(helper.subprocess, "run") as run:
            helper.initialize_linked_dns(self.result)
        run.assert_not_called()
        self.assertEqual("legacy", (helper.HUBDNS_DIR / "sub_apikey").read_text())
    def test_missing_or_old_package_does_not_block_linking(self):
        for responses in ([self.response(1)], [self.response(text="1.4.0"), self.response(1)]):
            with patch.object(helper.subprocess, "run", side_effect=responses) as run:
                helper.initialize_linked_dns(self.result)
            self.assertNotIn("dns_setup_error", self.result)
            self.assertFalse(any(c.args[0] == ["/usr/bin/hubdns-init"] for c in run.call_args_list))
    def test_dns_failure_preserves_link_and_explains_recovery(self):
        with patch.object(helper.subprocess, "run", side_effect=[self.response(), self.response(), self.response(1)]):
            helper.initialize_linked_dns(self.result)
        self.assertEqual("linked", self.result["connection_status"])
        self.assertIn("Run hubdns-init", self.result["dns_setup_error"])
    def test_cli_delegates_after_releasing_lock(self):
        held = []
        @contextlib.contextmanager
        def locked():
            held.append(True)
            try: yield
            finally: held.pop()
        client = MagicMock()
        client.linked.return_value = {}
        client.enroll.return_value = self.result
        def delegate(result):
            self.assertFalse(held)
            self.assertIs(result, self.result)
        with patch.object(helper, "Mail", return_value=client), patch.object(helper, "locked", locked), \
                patch.object(helper.sys, "argv", ["turnkey-hub", "enroll", "--token-stdin", "--json"]), \
                patch.object(helper.sys, "stdin", io.StringIO("private-fixture")), \
                patch.object(helper.sys, "stdout", io.StringIO()), patch.object(helper.os, "geteuid", return_value=0), \
                patch.object(helper, "initialize_linked_dns", side_effect=delegate) as init:
            self.assertEqual(0, helper.main())
        init.assert_called_once()
    def test_healthy_retry_does_not_delegate_or_probe_packages(self):
        client = MagicMock();client.linked.return_value = {"control_secret": "fixture"};client.retry.return_value = self.result
        with patch.object(helper, "Mail", return_value=client), patch.object(helper, "locked", contextlib.nullcontext), \
                patch.object(helper.sys, "argv", ["turnkey-hub", "retry", "--json"]), \
                patch.object(helper.sys, "stdout", io.StringIO()), patch.object(helper.os, "geteuid", return_value=0), \
                patch.object(helper, "initialize_linked_dns") as init:
            self.assertEqual(0, helper.main())
        init.assert_not_called()
