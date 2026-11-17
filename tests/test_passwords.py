"""Blank-input generation, confirmation and credential display boundaries."""
import os
import subprocess
import unittest
from unittest.mock import Mock, patch
from libinithooks import passwords
from test_dialog_wrapper import load_wrapper


class PasswordTest(unittest.TestCase):
    def setUp(self):
        self.module = load_wrapper()
        self.d = self.module.Dialog.__new__(self.module.Dialog)
        self.d.width, self.d.height = 60, 20
        self.d.wrapper = Mock()
        self.d.error = Mock()
        self.d.show_generated_password = Mock()

    def test_generated_values_satisfy_policy_and_are_independent(self):
        values = {passwords.generate(30, 4, ['-', '"', "'"]) for unused in range(20)}
        self.assertEqual(len(values), 20)
        self.assertTrue(all(passwords.valid(v, 30, 4, ['-', '"', "'"]) for v in values))

    def test_format_uses_four_uniform_word_draws_and_four_digits(self):
        words = passwords.wordlist()
        self.assertEqual(len(words), 7776)
        self.assertEqual(len(set(words)), 7776)
        with patch.object(passwords.secrets, "choice", side_effect=["acorn", "river", "candle", "harbor"]) as choice, \
             patch.object(passwords.secrets, "randbelow", return_value=7) as digits:
            self.assertEqual(passwords.generate(), "Acorn-river-candle-harbor-0007")
        digits.assert_called_once_with(10000)
        self.assertEqual(choice.call_count, 4)
        self.assertTrue(all(call.args == (words,) for call in choice.call_args_list))

    def test_impossible_policy_fails_instead_of_looping(self):
        with self.assertRaises(ValueError):
            passwords.generate(categories=5)

    def test_blank_generates_and_displays_without_confirmation(self):
        self.d.wrapper.return_value = ('ok', '')
        actual = self.d.get_password('System Password', 'Enter a password.')
        self.assertTrue(passwords.valid(actual))
        self.d.wrapper.assert_called_once()
        self.d.show_generated_password.assert_called_once_with('System Password', actual)
        self.assertIn('Leave blank', self.d.wrapper.call_args.args[1])

    def test_blank_confirmation_does_not_generate(self):
        self.d.wrapper.side_effect = [('ok', 'TypedPassword42'), ('ok', ''), ('ok', 'TypedPassword42'), ('ok', 'TypedPassword42')]
        self.assertEqual(self.d.get_password('Password', 'Enter a password.'), 'TypedPassword42')
        self.d.show_generated_password.assert_not_called()
        self.d.error.assert_called_once()
        self.assertNotIn('Leave blank', self.d.wrapper.call_args_list[1].args[1])

    def test_existing_credentials_require_input(self):
        self.d.wrapper.side_effect = [('ok', ''), ('ok', 'ExistingPassword42'), ('ok', 'ExistingPassword42')]
        with patch.object(self.module.sys, 'argv', ['/usr/lib/inithooks/bin/domain-controller.py']):
            self.assertEqual(self.d.get_password('Samba Password', 'Existing password'), 'ExistingPassword42')
        self.d.show_generated_password.assert_not_called()
        self.d.error.assert_called_once()

    def test_generated_display_keeps_password_off_command_line(self):
        secret = 'DisposablePassword42-example'
        captured = []
        def run(args, **kwargs):
            self.assertNotIn(secret, ' '.join(args))
            captured.append(os.read(kwargs['pass_fds'][0], 4096).decode())
            return subprocess.CompletedProcess(args, 0)
        self.d._handle_exitcode = Mock(return_value=True)
        with patch.object(self.module.subprocess, 'run', side_effect=run):
            self.module.Dialog.show_generated_password(self.d, 'Test', secret)
        self.assertIn(secret, captured[0])


if __name__ == '__main__':
    unittest.main()
