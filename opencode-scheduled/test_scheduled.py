import unittest
from contextlib import nullcontext
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
        with self.assertRaisesRegex(ValueError, "exactly one"):
            scheduled.validate_answers(result, [{"id": "known"}, {"id": "missing"}])
        with self.assertRaisesRegex(ValueError, "Unexpected review"):
            scheduled.validate_answers({"threads": result["threads"] * 2}, [{"id": "known"}])

    def test_at_cap_only_accepts_unchanged_answers(self):
        answers = {"threads": [{"id": "known", "fixed": True, "reply": "Already fixed."}]}
        target = {"answer_only": True, "failing": False}
        listed = [{"id": "known"}]
        scheduled.accept_fix("skip", answers, listed, target, changed=False)
        with self.assertRaisesRegex(ValueError, "unchanged checkout"):
            scheduled.accept_fix("skip", answers, listed, target, changed=True)
        with self.assertRaisesRegex(ValueError, "budget is exhausted"):
            scheduled.accept_fix("fix", answers, listed, target, changed=True)

    def test_answers_cannot_request_takeover(self):
        listed = [{"id": "known"}]
        answers = {"threads": [{"id": "known", "fixed": False, "reply": "  Needs a human: please check."}]}
        with self.assertRaisesRegex(ValueError, "reserved takeover prefix"):
            scheduled.validate_answers(answers, listed)
        answers["threads"][0]["reply"] = "This still needs a maintainer to check."
        scheduled.validate_answers(answers, listed)

    def test_exhaust_leases_deletion_to_the_validated_closed_head(self):
        target = {"number": 2, "branch": "opencode/nightly-1", "head": "a" * 40}
        with (patch.object(scheduled, "current", return_value={"commits": 3}) as current,
              patch.object(scheduled, "ci_failure", return_value=(10, 2)),
              patch.object(scheduled, "app_token", return_value=nullcontext("app")),
              patch.object(scheduled, "close", return_value=True) as close,
              patch.object(scheduled, "remote_head", return_value=target["head"]) as ref,
              patch.object(scheduled, "git"), patch.object(scheduled, "push") as push):
            scheduled.exhaust([target])
            current.assert_called_with(target, state="closed")
            self.assertTrue(close.call_args.kwargs["allowed"]())
            self.assertEqual(push.call_args.args, (target["branch"], "app", target["head"]))
            self.assertEqual(push.call_args.kwargs["source"], "")
            push.reset_mock()
            close.return_value = False
            scheduled.exhaust([target])
            push.assert_not_called()
            push.reset_mock()
            close.return_value = True
            ref.return_value = "b" * 40
            scheduled.exhaust([target])
            push.assert_not_called()

    def test_nightly_give_up_uses_leased_cleanup_and_retains_the_ci_check(self):
        target = {"number": 2, "branch": "opencode/nightly-1", "head": "a" * 40, "failing": True}
        result = {"decision": "give-up", "reason": "Needs redesign.", "answers": {"threads": []}}
        with (patch.object(scheduled, "check_fix", return_value=(10, 2)) as check,
              patch.object(scheduled, "write_context", return_value=""),
              patch.object(scheduled, "agent", return_value=result),
              patch.object(scheduled, "stage", return_value=False),
              patch.dict(scheduled.os.environ, SCHEMA=""),
              patch.object(scheduled, "app_token", return_value=nullcontext("app")),
              patch.object(scheduled, "close_nightly") as close):
            scheduled.fix(target, "Fix the PR.")
            self.assertEqual(close.call_args.args, (target, result["reason"], "app"))
            allowed = close.call_args.kwargs["allowed"]
            self.assertTrue(allowed())
            check.return_value = (10, 3)
            self.assertFalse(allowed())


if __name__ == "__main__":
    unittest.main()
