"""Check notification configuration when cron-apt has no local config."""
from pathlib import Path
import subprocess
import tempfile
import unittest


class SecurityAlertsShellTest(unittest.TestCase):
    def test_new_and_existing_aliases_rebuild_database(self):
        script = (Path(__file__).resolve().parents[1] / 'bin/secalerts.sh').read_text()
        functions = script.split('if [[ "$#"')[0]
        with tempfile.TemporaryDirectory() as directory:
            alias = Path(directory) / 'aliases'
            marker = Path(directory) / 'rebuilt'
            functions = functions.replace('/etc/aliases', str(alias))
            command = functions + f'\nnewaliases() {{ touch "{marker}"; }}\n'
            for email in ('first@example.net', 'next&name@example.net'):
                marker.unlink(missing_ok=True)
                result = subprocess.run(['bash', '-e', '-c', command + 'configure_alias root "$1"', '_', email], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(marker.exists())
                self.assertEqual(alias.read_text().strip(), f'root:    {email}')

    def test_missing_cron_apt_config_is_not_an_error(self):
        script = (Path(__file__).resolve().parents[1] / 'bin/secalerts.sh').read_text()
        functions = script.split('if [[ "$#"')[0]
        with tempfile.TemporaryDirectory() as directory:
            config = str(Path(directory) / 'config')
            functions = functions.replace('/etc/cron-apt/config', config)
            result = subprocess.run(
                ['bash', '-e', '-c', functions + '\nconfigure_cronapt MAILON output\n'],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
