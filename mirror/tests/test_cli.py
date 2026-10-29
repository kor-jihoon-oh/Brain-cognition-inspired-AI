"""Credential and CLI checks; no network, clipboard, or real API key access."""
from contextlib import redirect_stdout, redirect_stderr
import getpass
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'code'))
import start_gpt_with_key as cli
import api

# Deliberately synthetic and assembled so it cannot be confused with a live key.
TEST_KEY = 'sk-' + 'offline_test_only'


class CredentialsTest(unittest.TestCase):
    def test_environment_key_avoids_prompt(self):
        def forbidden(_):
            raise AssertionError('Must not prompt when an environment key exists')
        self.assertEqual(cli.readApiKey(environ={'OPENAI_API_KEY': TEST_KEY}, secret_input=forbidden), TEST_KEY)

    def test_missing_environment_key_uses_hidden_prompt(self):
        self.assertEqual(cli.readApiKey(environ={}, secret_input=lambda _: TEST_KEY), TEST_KEY)

    def test_non_echo_failure_does_not_fall_back(self):
        def unavailable(_):
            warnings.warn('Cannot hide input', getpass.GetPassWarning)
            raise AssertionError('Warning must stop input')
        with self.assertRaises(RuntimeError):
            cli.readApiKey(environ={}, secret_input=unavailable)

    def test_eof_is_safe(self):
        def unavailable(_):
            raise EOFError
        with self.assertRaises(RuntimeError):
            cli.readApiKey(environ={}, secret_input=unavailable)

    def test_clipboard_is_explicit_and_windows_only(self):
        with patch.object(cli, 'readClipboardApiKey', return_value=TEST_KEY) as reader, patch('builtins.input', return_value=''), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.readApiKey(clipboard=True, platform='nt', environ={'OPENAI_API_KEY': 'not-selected'}), TEST_KEY)
            reader.assert_called_once()
        with patch.object(cli, 'readClipboardApiKey') as reader:
            with self.assertRaises(ValueError):
                cli.readApiKey(clipboard=True, platform='posix')
            reader.assert_not_called()

    def test_validation_error_never_echoes_input(self):
        text = TEST_KEY + ' invalid private input'
        with self.assertRaises(ValueError) as error:
            api.validateKey(text)
        self.assertNotIn(text, str(error.exception))

    def test_transport_error_redacts_key_without_network(self):
        def failure(endpoint, request):
            raise RuntimeError('request failed for ' + TEST_KEY)
        with self.assertRaises(RuntimeError) as error:
            api.postJSON(body={}, key=TEST_KEY, requestImpl=failure)
        self.assertNotIn(TEST_KEY, str(error.exception))
        self.assertIn('[REDACTED]', str(error.exception))


class OfflineCommandsTest(unittest.TestCase):
    def assertOffline(self, args, operation, value):
        with patch.object(cli, 'readApiKey', side_effect=AssertionError('No key access allowed')), patch.object(cli, 'run', side_effect=AssertionError('No API call allowed')), patch.object(cli, operation, return_value=value) as op, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 0)
            op.assert_called_once()

    def test_dry_run_without_authentication(self):
        self.assertOffline(['--dry-run'], 'dryRun', {'api_calls': 0})

    def test_preview_without_authentication(self):
        self.assertOffline(['--preview', '1'], 'preview', 'public board')

    def test_inspect_without_authentication(self):
        with patch.object(cli, 'resolveResume', return_value='fixture'):
            self.assertOffline(['--inspect', '--resume', 'latest'], 'inspectResume', {'done': True})

    def test_completed_resume_without_authentication(self):
        with patch.object(cli, 'resolveResume', return_value='fixture'):
            self.assertOffline(['--resume', 'latest'], 'inspectResume', {'done': True})

    def test_help_without_authentication(self):
        with patch.object(cli, 'readApiKey', side_effect=AssertionError('No key access allowed')), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['--help']), 0)
        self.assertIn('--clipboard', output.getvalue())
        self.assertIn('OPENAI_API_KEY', output.getvalue())

    def test_live_error_is_redacted(self):
        with patch.object(cli, 'dryRun', return_value={}), patch.object(cli, 'readApiKey', return_value=TEST_KEY), patch.object(cli, 'createRun', return_value='fixture'), patch.object(cli, 'run', side_effect=RuntimeError('failed: ' + TEST_KEY)), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
            self.assertEqual(cli.main([]), 1)
        self.assertNotIn(TEST_KEY, error.getvalue())
        self.assertIn('[REDACTED]', error.getvalue())


if __name__ == '__main__':
    unittest.main()
