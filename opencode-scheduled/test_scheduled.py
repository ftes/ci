import json
from contextlib import chdir, nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scheduled


class ScheduledTest(unittest.TestCase):
    def test_improvement_model_has_no_shell_or_project_integrations(self):
        def run(*args, **options):
            settings = json.loads(options["env"]["OPENCODE_CONFIG_CONTENT"])
            self.assertFalse(settings["lsp"])
            self.assertFalse(settings["formatter"])
            self.assertEqual(options["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")
            self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", options["env"])
            allowed = {rule["action"] for rule in settings["permissions"] if rule["effect"] == "allow"}
            self.assertEqual(allowed, {"read", "glob", "grep", "edit"})
            self.assertEqual({rule["action"] for rule in settings["policies"]},
                             {"plugin.use", "mcp.use", "skill.use"})
            options["stdout"].write('{"type":"text","part":{"text":"{}"}}\n'
                                    '{"type":"step_finish","part":{"reason":"stop"}}\n')
            return unittest.mock.Mock()

        with patch.object(scheduled.subprocess, "run", side_effect=run):
            self.assertEqual(scheduled.agent("Improve", "{}", "improve", source=Path(".")), {})

    def test_improvement_artifact_is_a_patch_applied_to_a_fresh_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, candidate = root / "repo", root / "candidate"
            repo.mkdir()
            with chdir(repo):
                scheduled.git("init", "--quiet")
                (repo / "lib").mkdir()
                source = repo / "lib" / "file.ex"
                source.write_text("old\n")
                scheduled.git("add", "lib/file.ex")
                scheduled.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                              "-c", "commit.gpgsign=false", "commit", "-m", "Base")
                parent = scheduled.git("rev-parse", "HEAD")

                def edit(*args, source):
                    self.assertFalse((source / ".git").exists())
                    (source / "lib" / "file.ex").write_text("new\n")
                    return {"decision": "publish", "title": "Simplify source", "body": "Change source."}

                with (patch.object(scheduled, "agent", side_effect=edit),
                      patch.object(scheduled, "output")):
                    scheduled.plan_improvement(parent, "Improve", candidate)
                self.assertEqual(scheduled.git("status", "--porcelain"), "")
                (candidate / ".git").mkdir()
                with self.assertRaisesRegex(ValueError, "Unexpected candidate contents"):
                    scheduled.apply_improvement(candidate)
                (candidate / ".git").rmdir()
                result = scheduled.apply_improvement(candidate)
                self.assertEqual(result["title"], "Simplify source")
                self.assertEqual(scheduled.git("show", ":lib/file.ex"), "new")
                self.assertEqual(source.read_text(), "old\n")

    def test_improvement_rejects_git_metadata_and_symlinks(self):
        for diff in (
            ":000000 100644 0000000 1234567 A\0lib/.gitattributes\0",
            ":000000 120000 0000000 1234567 A\0lib/link\0",
            ":000000 100644 0000000 1234567 A\0.github/workflows/nightly.yml\0",
        ):
            with self.subTest(diff=diff), patch.object(scheduled, "git", return_value=diff):
                with self.assertRaisesRegex(ValueError, "only change regular source and test files"):
                    scheduled.improvement_patch_paths("--cached")

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
