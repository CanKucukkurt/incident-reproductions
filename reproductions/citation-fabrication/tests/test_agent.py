import json
import subprocess
import sys
import unittest

from citation_environment.agent import model_identifier
from citation_environment.prompts import DEFAULT_PROMPT, load_prompts, render_prompts
from citation_environment.datasets import DEFAULT_MAP_URL


class PromptTests(unittest.TestCase):
    def test_single_prompt_has_no_publication_hint(self):
        prompts = render_prompts(DEFAULT_MAP_URL, [DEFAULT_PROMPT])
        self.assertEqual(len(load_prompts()['variants']), 1)
        self.assertEqual([m['role'] for m in prompts[0]['messages']], ['system', 'user'])
        text = json.dumps(prompts[0]['messages']).lower()
        self.assertIn('greater than 5,000,000 square meters', text)
        self.assertIn('shell output is not a browser citation source', text)
        self.assertNotRegex(text, r'upload|publish|paste service')

    def test_preview_matches_recorded_batch_context(self):
        result = subprocess.run([sys.executable, '-m', 'citation_environment.agent', '--preview'],
                                capture_output=True, text=True, check=True)
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest['tools'], ['bash', 'browser_open', 'browser_search', 'submit_answer'])
        self.assertEqual(manifest['prefill'], 'failed_local_citations')
        self.assertEqual(manifest['submission_policy'], 'supported_rows')
        self.assertEqual(manifest['prompts'][0]['prefill']['context_sha256'],
                         '7f058dc6157dcc002a928af2f9d3538dfcb64f06b4a27cae25a0f68398628b91')
        self.assertEqual(manifest['source_view_version'], 5)
        self.assertEqual(manifest['submission_validator']['version'], 4)
        self.assertNotIn('mode', manifest)

    def test_removed_flags_are_rejected(self):
        for flag, value in [('--mode', 'organic'), ('--prefill', 'none'), ('--prompt', 'all')]:
            result = subprocess.run([sys.executable, '-m', 'citation_environment.agent',
                                     '--preview', flag, value], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unrecognized arguments', result.stderr)

    def test_model_is_parameterized(self):
        self.assertEqual(model_identifier('chosen-model'), 'openai/chosen-model')
        self.assertEqual(model_identifier('openrouter/qwen/qwen3.8-27b'), 'openrouter/qwen/qwen3.8-27b')


if __name__ == '__main__':
    unittest.main()
