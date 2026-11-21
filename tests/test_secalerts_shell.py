"""Check notification configuration when cron-apt has no local config."""
from pathlib import Path
import subprocess
import tempfile
import unittest


class SecurityAlertsShellTest(unittest.TestCase):
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
