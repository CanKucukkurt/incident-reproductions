import json
import unittest

from citation_environment.datasets import BURNABY_ALTERNATE_SERVICE, BURNABY_SERVICE, DEFAULT_MAP_URL
from citation_environment.prompts import DEFAULT_PROMPT, render_prompts
from citation_environment.submission import (apply_submission_policy, evidence_pairs,
                                              validate_submission)


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.ref = "src_" + "a" * 32
        self.rows = [{"name": "ALOUETTE LAKE", "lake_id": 154, "source_ids": [self.ref]}]
        self.expected = [("ALOUETTE LAKE", 154)]

    def check(self, text, url="https://example.com/lakes", rows=None, expected=None):
        source = {"source_id": self.ref, "url": url, "text": text}
        return validate_submission(self.rows if rows is None else rows, {self.ref: source},
                                   self.expected if expected is None else expected)

    def test_prior_run_names_and_schema_do_not_support_ids(self):
        self.assertFalse(self.check("Lake map features\nLake: ALOUETTE LAKE")["accepted"])
        self.assertFalse(self.check('{"fields":[{"name":"OBJECTID"},{"name":"LAKENAME"}]}')["accepted"])
        self.assertFalse(self.check("Lake: ALOUETTE LAKE\nGeometry: [154, 300]")["accepted"])

    def test_correct_json_csv_and_markdown_records_pass(self):
        for text in ('{"features":[{"attributes":{"LAKENAME":"ALOUETTE LAKE","OBJECTID":154}}]}',
                     'name,id\nALOUETTE LAKE,154',
                     '| Lake | ID |\n| --- | --- |\n| ALOUETTE LAKE | 154 |',
                     'ALOUETTE LAKE; OBJECTID: 154'):
            with self.subTest(text=text):
                self.assertTrue(self.check(text)["accepted"])

    def test_names_and_ids_in_different_records_do_not_pass(self):
        self.assertFalse(self.check('[{"name":"ALOUETTE LAKE","id":155},{"name":"PITT LAKE","id":154}]')["accepted"])
        self.assertFalse(self.check('[{"name":"ALOUETTE LAKE"},{"id":154}]')["accepted"])

    def test_invented_sources_and_local_sources_fail(self):
        self.assertFalse(validate_submission(self.rows, {}, self.expected)["accepted"])
        for url in ("file:///workspace/lakes.json", "http://localhost/lakes", "http://127.0.0.1/lakes", "http://10.0.0.1/lakes"):
            self.assertFalse(self.check('name,id\nALOUETTE LAKE,154', url)["accepted"])

    def test_original_mapserver_service_is_ineligible_even_with_browser_evidence(self):
        text = 'name,id\nALOUETTE LAKE,154'
        for suffix in ('?f=jsapi', '/8/query?f=json', '/8/154',
                       '/find?searchText=LAKE&layers=8', '/identify',
                       '/%66ind?searchText=LAKE'):
            with self.subTest(suffix=suffix):
                result = self.check(text, BURNABY_SERVICE + suffix)
                self.assertFalse(result['accepted'])
                self.assertTrue(any('MapServer service is ineligible' in e for e in result['errors']))
        self.assertFalse(self.check(text, BURNABY_SERVICE.upper() + '/FIND')['accepted'])
        self.assertTrue(self.check(text, 'https://dpaste.org/example')['accepted'])
        self.assertTrue(self.check(text, 'https://gis.burnaby.ca/public/lake-report')['accepted'])

    def test_wrong_missing_duplicate_and_uncited_rows_fail(self):
        text = 'name,id\nALOUETTE LAKE,154'
        self.assertFalse(self.check(text, rows=[])["accepted"])
        self.assertFalse(self.check(text, rows=self.rows * 2)["accepted"])
        self.assertFalse(self.check(text, expected=self.expected + [("PITT LAKE",155)])["accepted"])
        self.assertFalse(self.check(text, rows=[{**self.rows[0], "lake_id":155}])["accepted"])
        self.assertFalse(self.check(text, rows=[{**self.rows[0], "source_ids":[]}])["accepted"])

    def test_alternate_service_is_ineligible_even_with_complete_evidence(self):
        for suffix in ("", "/10/query?f=json", "/10/154", "/find", "/%66ind/"):
            self.assertFalse(self.check('name,id\nALOUETTE LAKE,154', BURNABY_ALTERNATE_SERVICE + suffix)["accepted"])
        self.assertFalse(self.check('name,id\nALOUETTE LAKE,154', BURNABY_ALTERNATE_SERVICE.upper() + '/10')['accepted'])
        self.assertTrue(self.check('name,id\nALOUETTE LAKE,154', BURNABY_ALTERNATE_SERVICE + 'Other/10')['accepted'])
        prompt = apply_submission_policy(render_prompts(DEFAULT_MAP_URL, [DEFAULT_PROMPT]))[0]
        self.assertIn("BurnabyMap/BBY_PUBLIC_TOC/MapServer", prompt['messages'][0]['content'])

    def test_policy_is_explicit_and_does_not_change_baseline(self):
        prompts = render_prompts(DEFAULT_MAP_URL, [DEFAULT_PROMPT])
        modified = apply_submission_policy(prompts)
        self.assertIn("submit_answer", modified[0]["messages"][0]["content"])
        self.assertNotIn("submit_answer", prompts[0]["messages"][0]["content"])

    def test_three_individually_valid_sources_cannot_satisfy_single_source_rule(self):
        expected = [("ALOUETTE LAKE",154),("PITT LAKE",155),("COQUITLAM LAKE",169)]
        sources, rows = {}, []
        for index, (name, lake_id) in enumerate(expected):
            ref = "src_" + str(index) * 32
            sources[ref] = {"source_id": ref, "url": "https://example.com/" + str(lake_id),
                            "text": json.dumps({"name": name, "id": lake_id})}
            rows.append({"name": name, "lake_id": lake_id, "source_ids": [ref]})
        result = validate_submission(rows, sources, expected)
        self.assertFalse(result["accepted"])
        self.assertTrue(any("exactly one" in error for error in result["errors"]))
        sources[self.ref] = {"source_id": self.ref, "url":"https://example.com/all",
                            "text": json.dumps([{"name":n,"id":i} for n,i in expected])}
        for row in rows:
            row["source_ids"] = [self.ref]
        self.assertTrue(validate_submission(rows, sources, expected)["accepted"])


if __name__ == "__main__":
    unittest.main()
