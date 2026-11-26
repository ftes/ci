import unittest
from unittest.mock import patch

import scheduled


class ScheduledTest(unittest.TestCase):
    def test_ci_ignores_other_heads_and_uses_only_the_latest_attempt(self):
        target = {"number": 2, "branch": "opencode/nightly-1", "head": "a" * 40}
        run = {"head_repository": {"full_name": "owner/project"}, "head_sha": target["head"],
               "pull_requests": [{"number": 2}], "status": "completed", "conclusion": "failure",
               "run_number": 5, "id": 10, "run_attempt": 2}
        other_head = run | {"head_sha": "b" * 40, "run_number": 6}
        rerun = run | {"run_number": 7, "status": "in_progress", "conclusion": None}
        with patch.object(scheduled, "REPO", "owner/project"), patch.object(scheduled, "api") as api:
            api.return_value = {"workflow_runs": [other_head, run]}
            self.assertEqual(scheduled.ci_failure(target), (10, 2))
            api.return_value = {"workflow_runs": [other_head, run, rerun]}
            self.assertIsNone(scheduled.ci_failure(target))

    def test_closing_rechecks_for_takeover_after_posting_the_reason(self):
        target = {"number": 2}
        with patch.object(scheduled, "current", side_effect=[True, False]), patch.object(scheduled, "api") as api:
            api.return_value = []
            scheduled.close(target, "Obsolete.", "credential")
            self.assertEqual(api.call_count, 2)
            self.assertEqual(api.call_args.kwargs["method"], "POST")

    def test_only_listed_findings_may_be_answered(self):
        result = {"threads": [{"id": "known", "fixed": True, "reply": "Fixed."}]}
        scheduled.validate_answers(result, [{"id": "known"}])
        with self.assertRaises(ValueError):
            scheduled.validate_answers(result, [{"id": "other"}])


if __name__ == "__main__":
    unittest.main()
