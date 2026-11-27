import json
from contextlib import chdir, nullcontext
from pathlib import Path
import subprocess
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
                (repo / "lib/retained.ex").write_text("keep\n")
                (repo / "lib/removed.ex").write_text("remove\n")
                (repo / "lib/version.ex").write_text('$Format:%H$\n')
                (repo / ".gitattributes").write_text("lib/retained.ex export-ignore\n"
                                                     "lib/version.ex export-subst\n")
                scheduled.git("add", ".")
                scheduled.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                              "-c", "commit.gpgsign=false", "commit", "-m", "Base")
                parent = scheduled.git("rev-parse", "HEAD")

                def edit(*args, source):
                    self.assertFalse((source / ".git").exists())
                    self.assertEqual((source / "lib/retained.ex").read_text(), "keep\n")
                    self.assertEqual((source / "lib/version.ex").read_text(), '$Format:%H$\n')
                    (source / "lib" / "file.ex").write_text("new\n")
                    (source / "lib/removed.ex").unlink()
                    return {"decision": "publish", "title": "Simplify source", "body": "Change source."}

                with (patch.object(scheduled, "agent", side_effect=edit),
                      patch.object(scheduled, "output")):
                    scheduled.plan_candidate(parent, "improve", [], "Improve", candidate)
                self.assertEqual(scheduled.git("status", "--porcelain"), "")
                (candidate / ".git").mkdir()
                with self.assertRaisesRegex(ValueError, "Unexpected candidate contents"):
                    scheduled.read_candidate(candidate)
                (candidate / ".git").rmdir()
                result, ci, patch_file = scheduled.read_candidate(candidate)
                scheduled.apply_patch(patch_file, "improve")
                self.assertEqual(result["title"], "Simplify source")
                self.assertEqual(scheduled.git("show", ":lib/file.ex"), "new")
                self.assertEqual(scheduled.git("diff", "--cached", "--name-status"),
                                 "M\tlib/file.ex\nD\tlib/removed.ex")
                self.assertEqual(source.read_text(), "old\n")

    def test_review_listing_and_answers_keep_the_publisher_checkout_clean(self):
        scripts = Path(__file__).resolve().parent.parent / "review-threads"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            threads = root / "scheduled-review-threads"
            output = root / "output"
            gh = root / "gh"
            gh.write_text("""#!/usr/bin/env bash
case "$*" in
  *addPullRequestReviewThreadReply*) echo reply ;;
  *) cat <<'JSON'
{"id":"thread","path":"lib/file.ex","line":1,"comments":{"nodes":[{"id":"root","author":{"login":"coderabbitai"},"body":"Fix this"}]}}
JSON
     ;;
esac
""")
            gh.chmod(0o755)
            answers = {"threads": [{"id": "thread", "fixed": False, "reply": "Explained."}]}
            env = {"REVIEW_THREADS_DIR": str(threads), "GITHUB_OUTPUT": str(output),
                   "PRS": "2", "REPO": "owner/project", "AUTHORS": "coderabbitai",
                   "ANSWERER": "github-actions", "MAX_ANSWERS": "2",
                   "SCHEMA": "enabled", "PR": "2", "ANSWERS": json.dumps(answers),
                   "PATH": f"{root}:{scheduled.os.environ['PATH']}"}
            with chdir(repo), patch.dict(scheduled.os.environ, env):
                scheduled.git("init", "--quiet")
                subprocess.run(["bash", scripts / "list.sh"], check=True, capture_output=True)
                self.assertEqual(scheduled.git("status", "--porcelain"), "")
                self.assertEqual(output.read_text(), "open=[2]\n")
                self.assertTrue((threads / "2.md").is_file())
                scheduled.validate_answers(answers, scheduled.findings_manifest(2))
                reply = subprocess.check_output(["bash", scripts / "answer.sh"], text=True)
                self.assertIn("Thread thread: declined", reply)

    def test_improvement_rejects_git_metadata_and_symlinks(self):
        for diff in (
            ":000000 100644 0000000 1234567 A\0lib/.gitattributes\0",
            ":000000 120000 0000000 1234567 A\0lib/link\0",
            ":000000 100644 0000000 1234567 A\0.github/workflows/nightly.yml\0",
        ):
            with self.subTest(diff=diff), patch.object(scheduled, "git", return_value=diff):
                with self.assertRaisesRegex(ValueError, "only change regular source"):
                    scheduled.candidate_paths("improve", "--cached")

    def test_recovery_requires_the_exact_checked_artifact(self):
        branch, parent = "opencode/nightly-123", "a" * 40
        title, body = "Simplify", "Explain."
        message = f"{body}\n\nOpenCode-Scheduled: {branch} {parent}"
        record = {"message": f"{title}\n\n{message}\n", "author": {"email": scheduled.BOT_EMAIL},
                  "parents": [{"sha": parent}], "tree": {"sha": "checked-tree"}}
        result = {"decision": "publish", "title": title, "body": body}
        with (patch.dict(scheduled.os.environ, GITHUB_RUN_ID="123", GITHUB_STEP_SUMMARY="/dev/null"),
              patch.object(scheduled, "BASE", "main"),
              patch.object(scheduled, "remote_head", side_effect=lambda ref: parent if ref == "main" else "pushed"),
              patch.object(scheduled, "branch_prs", return_value=[]),
              patch.object(scheduled, "nightly_open", return_value=0),
              patch.object(scheduled, "git", side_effect=[parent, "different-tree", parent, "checked-tree"]),
              patch.object(scheduled, "api", return_value=record) as api,
              patch.object(scheduled, "app_token", return_value=nullcontext("app")) as token,
              patch.object(scheduled, "published_pr_matches", return_value=True),
              patch.object(scheduled, "apply_patch") as applied):
            with self.assertRaisesRegex(ValueError, "retry only the failed publisher .*artifact is still available"):
                scheduled.publish([], "improve", result, Path("candidate-b.patch"))
            applied.assert_called_once_with(Path("candidate-b.patch"), "improve")
            token.assert_not_called()
            api.side_effect = [record, {"state": "open", "html_url": "https://example.invalid/pr"}]
            scheduled.publish([], "improve", result, Path("candidate-a.patch"))
            applied.assert_called_with(Path("candidate-a.patch"), "improve")
            self.assertEqual(api.call_args.kwargs["method"], "POST")

    def test_original_stays_open_if_replacement_changes_after_the_close_comment(self):
        repo, parent, head = "owner/project", "a" * 40, "b" * 40
        target = {"number": 2, "branch": "opencode/nightly-1", "head": "c" * 40}
        branch = "opencode/nightly-123-2"
        replacement = {"number": 3, "state": "open", "html_url": "https://example.invalid/pr",
                       "merged_at": None, "user": {"type": "Bot", "login": scheduled.BOT},
                       "head": {"ref": branch, "sha": head, "repo": {"full_name": repo}},
                       "base": {"ref": "main", "sha": parent, "repo": {"full_name": repo}}}
        result = {"decision": "publish", "title": "Replace", "body": "Updated."}
        changes = {"closed": {"state": "closed"},
                   "head moved": {"head": replacement["head"] | {"sha": "d" * 40}},
                   "base moved": {"base": replacement["base"] | {"sha": "e" * 40}},
                   "unchanged": {}}
        for recover in (False, True):
            for name, change in changes.items():
                heads = {"main": parent, branch: head if recover else None}
                with (self.subTest(recover=recover, change=name),
                      patch.dict(scheduled.os.environ, GITHUB_RUN_ID="123", GITHUB_STEP_SUMMARY="/dev/null"),
                      patch.object(scheduled, "BASE", "main"), patch.object(scheduled, "REPO", repo),
                      patch.object(scheduled, "remote_head", side_effect=heads.get),
                      patch.object(scheduled, "current", return_value=True),
                      patch.object(scheduled, "branch_prs", return_value=[replacement] if recover else []),
                      patch.object(scheduled, "apply_patch"), patch.object(scheduled, "verify_recovery"),
                      patch.object(scheduled, "commit"),
                      patch.object(scheduled, "push", side_effect=lambda branch, *_: heads.update({branch: head})),
                      patch.object(scheduled, "git", side_effect=[parent, "tree" if recover else head]),
                      patch.object(scheduled, "app_token", return_value=nullcontext("app")),
                      patch.object(scheduled, "api") as api):
                    api.side_effect = [
                        *([] if recover else [replacement]),
                        replacement, [], {}, replacement | change, {"state": "closed"},
                    ]
                    scheduled.publish([target], "recreate", result, Path("candidate.patch"))
                    api.assert_any_call("pulls/3", token="app")
                    writes = [call for call in api.call_args_list if call.kwargs.get("method") == "PATCH"]
                    self.assertEqual([call.args for call in writes], [] if change else [("pulls/2",)])

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
              patch.dict(scheduled.os.environ, SCHEMA=""),
              patch.object(scheduled, "app_token", return_value=nullcontext("app")),
              patch.object(scheduled, "close_nightly") as close):
            scheduled.fix(target, result, None, [10, 2])
            self.assertEqual(close.call_args.args, (target, result["reason"], "app"))
            allowed = close.call_args.kwargs["allowed"]
            self.assertTrue(allowed())
            check.return_value = (10, 3)
            self.assertFalse(allowed())


if __name__ == "__main__":
    unittest.main()
