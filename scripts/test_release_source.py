"""Security boundary tests use synthetic GitHub responses; no network or tokens."""

from __future__ import annotations

import copy
import unittest
from urllib.parse import urlencode

from check_release_source import (
    ACTIONS_APP_ID,
    API_ROOT,
    REPOSITORY,
    REQUIRED_JOBS,
    ReleasePolicyError,
    authorize_release,
)

RELEASE_SHA = "a" * 40
MAIN_SHA = "b" * 40
TAG_SHA = "c" * 40
RUN_ID = 101
SUITE_ID = 202
RUNS_PATH = "/actions/workflows/ci.yml/runs?" + urlencode({
    "event": "push", "branch": "main", "head_sha": RELEASE_SHA, "per_page": 100,
})
JOBS_PATH = f"/actions/runs/{RUN_ID}/jobs?filter=latest&per_page=100&page=1"
COMPARE_PATH = f"/compare/{RELEASE_SHA}...{MAIN_SHA}"


def evidence():
    jobs = []
    responses = {
        "/git/ref/tags/ts-v1.2.3": {
            "ref": "refs/tags/ts-v1.2.3", "object": {"type": "commit", "sha": RELEASE_SHA},
        },
        "/branches/main": {"name": "main", "protected": True, "commit": {"sha": MAIN_SHA}},
        COMPARE_PATH: {"status": "ahead", "merge_base_commit": {"sha": RELEASE_SHA}},
        RUNS_PATH: {"workflow_runs": [{
            "id": RUN_ID, "head_sha": RELEASE_SHA, "event": "push", "head_branch": "main",
            "path": ".github/workflows/ci.yml", "repository": {"full_name": REPOSITORY},
            "head_repository": {"full_name": REPOSITORY}, "status": "completed",
            "conclusion": "success", "run_attempt": 2, "check_suite_id": SUITE_ID,
        }]},
        JOBS_PATH: {"jobs": jobs},
    }
    for check_id, name in enumerate(REQUIRED_JOBS, start=300):
        jobs.append({
            "name": name, "run_id": RUN_ID, "run_attempt": 2, "head_sha": RELEASE_SHA,
            "status": "completed", "conclusion": "success",
            "check_run_url": f"{API_ROOT}/check-runs/{check_id}",
        })
        responses[f"/check-runs/{check_id}"] = {
            "app": {"id": ACTIONS_APP_ID, "slug": "github-actions"},
            "name": name, "head_sha": RELEASE_SHA, "status": "completed", "conclusion": "success",
            "check_suite": {"id": SUITE_ID},
        }
    return responses


class ReleaseSourceTests(unittest.TestCase):
    def setUp(self):
        self.responses = evidence()
        self.calls = []
        self.args = {
            "repository": REPOSITORY, "event": "push", "ref": "refs/tags/ts-v1.2.3",
            "sha": RELEASE_SHA, "tag_prefix": "ts-v",
        }

    def read(self, path):
        self.calls.append(path)
        if path not in self.responses:
            raise ReleasePolicyError("Evidence unavailable")
        return self.responses[path]

    def authorize(self):
        return authorize_release(self.read, **self.args)

    def deny(self):
        with self.assertRaises(ReleasePolicyError):
            self.authorize()

    def test_release_tag_with_main_ancestry_and_exact_ci_is_allowed(self):
        self.assertEqual(self.authorize(), {"sha": RELEASE_SHA, "ci_run_id": RUN_ID})

    def test_manual_main_release_with_older_reviewed_sha_is_allowed(self):
        self.args.update(event="workflow_dispatch", ref="refs/heads/main")
        self.authorize()
        self.assertNotIn("/git/ref/tags/ts-v1.2.3", self.calls)

    def test_annotated_release_tag_is_resolved_to_commit(self):
        self.responses["/git/ref/tags/ts-v1.2.3"]["object"] = {"type": "tag", "sha": TAG_SHA}
        self.responses[f"/git/tags/{TAG_SHA}"] = {"object": {"type": "commit", "sha": RELEASE_SHA}}
        self.authorize()

    def test_identical_main_commit_is_allowed(self):
        self.responses[COMPARE_PATH]["status"] = "identical"
        self.authorize()

    def test_untrusted_refs_events_repositories_and_sha_fail_before_api(self):
        changes = [
            {"repository": "attacker/autocontext"}, {"event": "pull_request"},
            {"event": "pull_request_target"}, {"event": "workflow_run"},
            {"event": "workflow_dispatch", "ref": "refs/heads/feature"},
            {"ref": "refs/heads/main"}, {"ref": "refs/tags/py-v1.2.3"},
            {"ref": "refs/tags/ts-v"}, {"ref": "refs/tags/ts-v1;echo-bad"},
            {"ref": "refs/tags/ts-v1/../../main"}, {"sha": "a" * 7},
            {"sha": RELEASE_SHA + "\n"}, {"tag_prefix": "ts-v.*"},
        ]
        for change in changes:
            with self.subTest(change=change):
                args = self.args | change
                with self.assertRaises(ReleasePolicyError):
                    authorize_release(self.read, **args)
        self.assertEqual(self.calls, [])

    def test_moved_tag_cannot_change_build_source(self):
        self.responses["/git/ref/tags/ts-v1.2.3"]["object"]["sha"] = MAIN_SHA
        self.deny()

    def test_wrong_ref_response_is_rejected(self):
        self.responses["/git/ref/tags/ts-v1.2.3"]["ref"] = "refs/tags/ts-v9"
        self.deny()

    def test_cyclic_annotated_tag_fails(self):
        self.responses["/git/ref/tags/ts-v1.2.3"]["object"] = {"type": "tag", "sha": TAG_SHA}
        self.responses[f"/git/tags/{TAG_SHA}"] = {"object": {"type": "tag", "sha": TAG_SHA}}
        self.deny()

    def test_non_commit_tag_target_fails(self):
        self.responses["/git/ref/tags/ts-v1.2.3"]["object"]["type"] = "tree"
        self.deny()

    def test_unprotected_main_is_rejected(self):
        self.responses["/branches/main"]["protected"] = False
        self.deny()

    def test_divergent_or_ahead_of_main_release_is_rejected(self):
        for value in [{"status": "diverged"}, {"status": "behind"},
                      {"merge_base_commit": {"sha": MAIN_SHA}}]:
            with self.subTest(value=value):
                self.responses[COMPARE_PATH] = {"status": "ahead", "merge_base_commit": {"sha": RELEASE_SHA}} | value
                self.deny()

    def test_ci_run_must_match_workflow_repo_event_branch_and_sha(self):
        original = copy.deepcopy(self.responses[RUNS_PATH]["workflow_runs"][0])
        for change in [{"head_sha": MAIN_SHA}, {"event": "pull_request"}, {"head_branch": "feature"},
                       {"path": ".github/workflows/spoof.yml"},
                       {"repository": {"full_name": "attacker/autocontext"}},
                       {"head_repository": {"full_name": "attacker/autocontext"}}]:
            with self.subTest(change=change):
                self.responses[RUNS_PATH]["workflow_runs"] = [original | change]
                self.deny()

    def test_old_success_cannot_mask_latest_failed_or_in_progress_run(self):
        original = self.responses[RUNS_PATH]["workflow_runs"][0]
        for change in [{"conclusion": "failure"}, {"status": "in_progress", "conclusion": None}]:
            with self.subTest(change=change):
                self.responses[RUNS_PATH]["workflow_runs"] = [original, original | {"id": RUN_ID + 1} | change]
                self.deny()

    def test_missing_and_duplicate_required_job_are_rejected(self):
        original = copy.deepcopy(self.responses[JOBS_PATH]["jobs"])
        for jobs in [original[:-1], original + [original[0]]]:
            with self.subTest(count=len(jobs)):
                self.responses[JOBS_PATH]["jobs"] = jobs
                self.deny()

    def test_required_job_must_be_successful_and_same_sha_run(self):
        original = copy.deepcopy(self.responses[JOBS_PATH]["jobs"][0])
        for change in [{"conclusion": "skipped"}, {"conclusion": "failure"},
                       {"status": "in_progress"}, {"head_sha": MAIN_SHA},
                       {"run_id": RUN_ID + 1}, {"run_attempt": 3}]:
            with self.subTest(change=change):
                self.responses[JOBS_PATH]["jobs"][0] = original | change
                self.deny()

    def test_successful_previous_attempt_job_in_successful_partial_rerun_is_allowed(self):
        self.responses[JOBS_PATH]["jobs"][0]["run_attempt"] = 1
        self.authorize()

    def test_check_url_cannot_send_token_to_other_host_or_repository(self):
        for url in ["https://attacker.example/check-runs/1", "https://api.github.com/repos/attacker/x/check-runs/1",
                    API_ROOT + "/check-runs/300?token=bad", API_ROOT + "/check-runs/300/../secret"]:
            with self.subTest(url=url):
                self.responses[JOBS_PATH]["jobs"][0]["check_run_url"] = url
                self.deny()
                self.assertNotIn(url, self.calls)

    def test_forged_check_name_other_app_suite_or_sha_is_rejected(self):
        original = copy.deepcopy(self.responses["/check-runs/300"])
        for change in [{"app": {"id": 123, "slug": "github-actions"}},
                       {"app": {"id": ACTIONS_APP_ID, "slug": "spoof"}},
                       {"head_sha": MAIN_SHA}, {"name": "spoof"},
                       {"check_suite": {"id": SUITE_ID + 1}},
                       {"conclusion": "neutral"}, {"status": "in_progress"}]:
            with self.subTest(change=change):
                self.responses["/check-runs/300"] = original | change
                self.deny()

    def test_api_failure_denies_release(self):
        del self.responses["/check-runs/300"]
        self.deny()

    def test_missing_ci_denies_release(self):
        self.responses[RUNS_PATH]["workflow_runs"] = []
        self.deny()

    def test_jobs_are_paginated(self):
        jobs = self.responses[JOBS_PATH]["jobs"]
        self.responses[JOBS_PATH]["jobs"] = [{"name": "other"}] * 100
        self.responses[JOBS_PATH.removesuffix("page=1") + "page=2"] = {"jobs": jobs}
        self.authorize()


if __name__ == "__main__":
    unittest.main()
