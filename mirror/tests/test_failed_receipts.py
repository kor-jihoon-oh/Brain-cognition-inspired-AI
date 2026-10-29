"""Ensure API error receipts cannot persist the key used by the request."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'code'))
import runner

TEST_KEY = 'sk-' + 'offline_test_only'


class FailedReceiptTests(unittest.TestCase):
    def runFailure(self, response):
        with tempfile.TemporaryDirectory(prefix='mirror-error-test-') as temp:
            folder = Path(temp)
            state = {'done': False, 'problemIndex': 0, 'roundIndex': 0, 'attempt': 1}
            loaded = {'config': {}, 'dataset': {'problems': [{'problem_number': 1, 'problem_id': 'fixture'}]}, 'prompt': '', 'pricing': {}}
            request = {'input': [{'content': 'public board'}], 'model': 'fixture'}
            with patch.object(runner, 'loadRun', return_value=loaded), patch.object(runner, 'replay', return_value=state), patch.object(runner, 'exportReports', return_value={}), patch.object(runner, 'buildRequest', return_value=request), patch.object(runner, 'priceUsage', return_value={'cost_status':'usage_missing','estimated_cost_usd':None}):
                with self.assertRaises(RuntimeError) as error:
                    runner.run(folder, {'apiKey': TEST_KEY, 'send': lambda _: response, 'log': lambda _: None})
            texts = [p.read_text(encoding='utf-8') for p in folder.rglob('*.json')]
            self.assertTrue(texts)
            self.assertTrue(all(TEST_KEY not in text for text in texts))
            self.assertNotIn(TEST_KEY, str(error.exception))
            call = json.loads((folder/'calls/00001.json').read_text(encoding='utf-8'))
            self.assertEqual(call['status'], 'api_error')
            self.assertEqual(call['http_status'], response['status'])
            self.assertEqual(call['request_id'], 'req_fixture')
            self.assertTrue(call['credential_redacted'])
            if call.get('response'):
                self.assertNotIn(TEST_KEY, json.dumps(call['response']))
                self.assertNotIn(TEST_KEY, json.dumps(json.loads(call['raw_http_body'])))
                self.assertEqual(call['response_sha256'], runner.hash(call['response']))
            return call

    def test_401_echo_in_error_and_nested_metadata(self):
        body = {'error': {'message': 'Incorrect key: '+TEST_KEY, 'code':'invalid_api_key', 'nested': [TEST_KEY, {TEST_KEY:'echoed'}]}}
        call=self.runFailure({'status':401,'request_id':'req_fixture','text':json.dumps(body)})
        self.assertEqual(call['response']['error']['code'], 'invalid_api_key')
        self.assertEqual(call['response']['error']['message'], 'Incorrect key: [REDACTED]')

    def test_json_escaped_key_is_removed_from_raw_and_parsed_content(self):
        escaped=''.join('\\u%04x'%ord(char) for char in TEST_KEY)
        body='{"error":{"message":"Incorrect key: '+escaped+'","code":"invalid_api_key"}}'
        self.runFailure({'status':401,'request_id':'req_fixture','text':body})

    def test_plain_text_error_echo(self):
        call=self.runFailure({'status':401,'request_id':'req_fixture','text':'Unauthorized: '+TEST_KEY})
        self.assertEqual(call['raw_http_body'], 'Unauthorized: [REDACTED]')

    def test_success_receipt_is_untouched(self):
        call={'status':'completed','raw_http_body':'opaque '+TEST_KEY,'response':{'encrypted_content':TEST_KEY},'response_sha256':'existing'}
        before=json.dumps(call,sort_keys=True)
        runner._redactFailedReceipt(call, TEST_KEY)
        self.assertEqual(json.dumps(call,sort_keys=True),before)

    def test_unrelated_key_like_strings_are_not_redacted(self):
        other='sk-'+'different_synthetic_content'
        call={'status':'api_error','raw_http_body':other,'response':{'error':{'message':other}}}
        before=json.dumps(call,sort_keys=True)
        runner._redactFailedReceipt(call, TEST_KEY)
        self.assertEqual(json.dumps(call,sort_keys=True),before)


if __name__ == '__main__':
    unittest.main()
