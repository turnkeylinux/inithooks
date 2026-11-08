#!/usr/bin/env python3
"""Exercise the shared dialog contract without a terminal or system log access."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_wrapper():
    spec = importlib.util.spec_from_file_location(
        "inithooks_dialog_under_test", ROOT / "libinithooks/dialog_wrapper.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"dialog": types.ModuleType("dialog")}), \
            patch("logging.basicConfig"):
        spec.loader.exec_module(module)
    return module


class DialogWrapperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_wrapper()

    def setUp(self):
        self.console = types.SimpleNamespace(
            OK="ok", CANCEL="cancel", ESC="esc", yesno=Mock(),
            passwordbox=Mock(), inputbox=Mock(), msgbox=Mock())
        self.dialog = self.module.Dialog.__new__(self.module.Dialog)
        self.dialog.console = self.console
        # Fail promptly if the old recursive error path is reached.
        self.dialog.msgbox = Mock(side_effect=AssertionError(
            "Backend errors must not open another dialog"))

    def test_escape_confirms_quit_for_scalar_and_tuple_results(self):
        for result in ("esc", ("esc", "discarded input")):
            with self.subTest(result=result):
                self.console.passwordbox.return_value = result
                self.console.yesno.return_value = "ok"
                self.console.yesno.reset_mock()
                with self.assertRaises(SystemExit) as caught:
                    self.dialog.wrapper("passwordbox", "Password")
                self.assertEqual(caught.exception.code, 130)
                self.console.yesno.assert_called_once_with(
                    "Do you really want to quit?")

    def test_declined_quit_reopens_widget_and_preserves_result(self):
        for escaped, accepted in (("esc", "ok"),
                                  (("esc", ""), ("ok", "new answer"))):
            with self.subTest(escaped=escaped):
                self.console.inputbox.reset_mock()
                self.console.inputbox.side_effect = [escaped, accepted]
                self.console.yesno.return_value = "cancel"
                actual = self.dialog.wrapper("inputbox", "Question", init="old")
                self.assertIs(actual, accepted)
                self.assertEqual(self.console.inputbox.call_count, 2)
                self.console.inputbox.assert_called_with("\nQuestion", init="old")

    def test_success_and_cancel_results_retain_their_shape(self):
        for result in ("ok", "cancel", ("ok", "answer"), ("cancel", "")):
            with self.subTest(result=result):
                self.console.inputbox.return_value = result
                self.assertIs(self.dialog.wrapper("inputbox", "Question"), result)
        self.console.yesno.assert_not_called()

    def test_status_handler_accepts_input_widget_result_directly(self):
        self.console.yesno.return_value = "cancel"
        self.assertFalse(self.dialog._handle_exitcode(("esc", "discarded")))
        self.console.yesno.assert_called_once()

    def test_widget_backend_failure_propagates_once_without_error_dialog(self):
        failure = RuntimeError("terminal backend unavailable")
        self.console.passwordbox.side_effect = failure
        with self.assertRaises(RuntimeError) as caught:
            self.dialog.wrapper("passwordbox", "Password")
        self.assertIs(caught.exception, failure)
        self.console.passwordbox.assert_called_once()
        self.dialog.msgbox.assert_not_called()
        self.console.msgbox.assert_not_called()

    def test_quit_confirmation_failure_propagates_without_error_dialog(self):
        failure = RuntimeError("terminal disappeared")
        self.console.passwordbox.return_value = ("esc", "")
        self.console.yesno.side_effect = failure
        with self.assertRaises(RuntimeError) as caught:
            self.dialog.wrapper("passwordbox", "Password")
        self.assertIs(caught.exception, failure)
        self.console.passwordbox.assert_called_once()
        self.console.yesno.assert_called_once()
        self.dialog.msgbox.assert_not_called()

    def test_authenticated_account_email_is_editable_in_generic_app_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.module.onboarding, 'CACHE_DIR', Path(directory)), \
                    patch.object(self.module.onboarding, 'observe'):
                self.module.onboarding.remember_email('owner@example.net')
                self.dialog.inputbox = Mock(return_value=('ok', 'app-admin@example.net'))
                self.assertEqual(self.dialog.get_email('App email', 'Email:', 'admin@example.com'),
                                 'app-admin@example.net')
                self.assertEqual(self.dialog.inputbox.call_args.args[2], 'owner@example.net')
                self.dialog.get_email('App email', 'Email:', 'existing@example.net')
                self.assertEqual(self.dialog.inputbox.call_args.args[2], 'existing@example.net')

    def test_debug_log_excludes_answers_and_widget_arguments(self):
        secret = "DoNotLogThisAnswer42!"
        result = ("ok", secret)
        self.console.passwordbox.return_value = result
        with self.assertLogs(level="DEBUG") as logged:
            self.assertIs(self.dialog.wrapper(
                "passwordbox", secret, secret, init=secret), result)
            self.assertTrue(self.dialog._handle_exitcode(result))
        self.assertNotIn(secret, "\n".join(logged.output))

    def test_backend_exception_details_are_not_logged_by_wrapper(self):
        secret = "DoNotLogThisAnswer42!"
        self.console.passwordbox.side_effect = RuntimeError(secret)
        with self.assertLogs(level="DEBUG") as logged:
            with self.assertRaises(RuntimeError):
                self.dialog.wrapper("passwordbox", "Password", init=secret)
        self.assertNotIn(secret, "\n".join(logged.output))

    def test_unsupported_widget_keeps_specific_error(self):
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(self.module.Error) as caught:
                self.dialog.wrapper("nonexistent", "Question")
        self.assertEqual(str(caught.exception), "dialog not supported: nonexistent")


if __name__ == "__main__":
    unittest.main()
