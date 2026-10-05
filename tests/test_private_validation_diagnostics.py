"""Public, synthetic-only checks of the workflow's bounded private summary."""
from pathlib import Path
import re
import textwrap
import unittest


workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/private-validation.yml').read_text(encoding='utf-8')
body = workflow.split('          # PRIVATE_DIAGNOSTIC_FUNCTION_BEGIN\n', 1)[1].split('          # PRIVATE_DIAGNOSTIC_FUNCTION_END', 1)[0]
namespace = {'re': re}
exec(compile(textwrap.dedent(body), '<workflow-summary>', 'exec'), namespace)
describe = namespace['diagnostic_description']


class Diagnostics(unittest.TestCase):
    def test_node_failure_files_are_bounded_and_private_only(self):
        identify = namespace['diagnostic_failure_ids']
        report = {'schema': 'hashmm.private-ci-diagnostic.v1', 'suite': 'desktop-node',
                  'steps': [{'stage': 'desktop-tests', 'status': 'failed',
                             'test_file': 'desktop/tests-node/test_synthetic.js',
                             'output': 'synthetic-secret'}]}
        self.assertEqual(identify(report, 'desktop-node', 'failure'), ['desktop/tests-node/test_synthetic.js'])
        self.assertEqual(identify(report, 'desktop-node', 'success'), [])
        self.assertNotIn('test_synthetic', describe(report, 'desktop-node', 'failure'))
        for unsafe in ('../private.js', 'desktop/tests-node/../../secret.js',
                       'desktop/tests-node/test_x.js?token=synthetic', 'desktop/tests-node/test_x.js\nsecret',
                       'desktop/tests-node/' + 'x' * 140 + '.js', None):
            report['steps'][0]['test_file'] = unsafe
            self.assertEqual(identify(report, 'desktop-node', 'failure'), [])
        report['steps'] = [{'stage': 'desktop-tests', 'status': 'failed',
                            'test_file': f'desktop/tests-node/test_synthetic_{i}.js'} for i in range(12)]
        self.assertEqual(len(identify(report, 'desktop-node', 'failure')), 10)
        report['steps'][0]['status'] = 'passed'
        self.assertNotIn('desktop/tests-node/test_synthetic_0.js', identify(report, 'desktop-node', 'failure'))

    def report(self, **updates):
        return {'schema': 'hashmm.private-ci-diagnostic.v1', 'suite': 'python',
                'steps': [{'stage': 'pytest', 'status': 'failed'}], **updates}

    def test_missing_report_is_not_a_pass(self):
        self.assertIn('unavailable', describe(None, 'python', 'failure'))

    def test_success_comes_from_step_outcome(self):
        self.assertEqual(describe(None, 'python', 'success'), 'Private suite passed.')

    def test_wrong_suite_is_rejected(self):
        self.assertIn('unavailable', describe(self.report(suite='iteration'), 'python', 'failure'))

    def test_raw_details_and_parameterized_ids_are_not_forwarded(self):
        report = self.report(pytest={'status': 'available', 'failed': 1, 'errors': 0,
            'message': 'synthetic-private-message',
            'test_ids': ['tests.test_sample::test_failure[synthetic-private-parameter]']})
        result = describe(report, 'python', 'failure')
        self.assertEqual(result, 'pytest: failed=1 errors=0')
        self.assertNotIn('private', result)

    def test_only_safe_identifier_is_forwarded(self):
        report = self.report(pytest={'status': 'available', 'failed': 1, 'errors': 0,
            'test_ids': ['bad/newline\nvalue', 'tests.test_sample::test_failure']})
        self.assertEqual(describe(report, 'python', 'failure'),
                         'pytest: failed=1 errors=0 tests.test_sample::test_failure')

    def test_unrecognized_stage_cannot_become_a_message(self):
        self.assertIn('unavailable', describe(self.report(steps=[{'stage': 'synthetic-private-value', 'status': 'failed'}]), 'python', 'failure'))

    def test_invalid_counts_are_not_serialized(self):
        result = describe(self.report(pytest={'status': 'available', 'failed': 'synthetic-private-value', 'errors': False}), 'python', 'failure')
        self.assertEqual(result, 'pytest: failed')

    def test_description_has_bounded_length(self):
        report = self.report(pytest={'status': 'available', 'failed': 1, 'errors': 0,
            'test_ids': ['tests.test_'+'x'*150+'::test_failure']})
        self.assertLessEqual(len(describe(report, 'python', 'failure')), 140)

    def test_interruption_keeps_active_test_distinct_from_assertion_failure(self):
        result = describe(self.report(steps=[{'stage': 'pytest', 'status': 'running'}],
            pytest={'status': 'in_progress', 'completed': 7,
                    'test_ids': ['tests.test_sample::test_slow']}), 'python', 'failure')
        self.assertEqual(result, 'pytest: interrupted completed=7 tests.test_sample::test_slow')

    def test_interruption_never_forwards_parameter_or_invalid_count(self):
        result = describe(self.report(pytest={'status': 'in_progress',
            'completed': 'synthetic-private-count',
            'test_ids': ['tests.test_sample::test_slow[synthetic-private-value]']}), 'python', 'failure')
        self.assertEqual(result, 'pytest: interrupted')

    def test_finishing_without_final_result_is_not_a_pass(self):
        result = describe(self.report(pytest={'status': 'finishing', 'completed': 20,
            'test_ids': []}), 'python', 'failure')
        self.assertEqual(result, 'pytest: interrupted completed=20')

    def test_status_secret_is_confined_to_post_test_step(self):
        suite_step = workflow.split('      - name: Run full suite with private output', 1)[1].split('      - name: Send bounded diagnostic', 1)[0]
        self.assertNotIn('STATUS_TOKEN', suite_step)
        self.assertNotIn('HASHMM_STATUS_WRITE', suite_step)
        self.assertNotIn('print(payload)', workflow)
        self.assertNotIn('print(report)', workflow)


if __name__ == '__main__':
    unittest.main()
