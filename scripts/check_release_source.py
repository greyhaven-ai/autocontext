#!/usr/bin/env python3
"""Authorize a release SHA using protected main and a successful same-SHA CI run.

Run this copy from protected main, before checking out/building release source.
Tag creation rules and registry OIDC environment restrictions remain necessary:
a guard in workflow YAML cannot stop a writer who can replace that YAML.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

REPOSITORY = "greyhaven-ai/autocontext"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
ACTIONS_APP_ID = 15368
REQUIRED_JOBS = ("lint", "test", "smoke", "dependency-security", "ts-lint", "ts-test")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
TAG_PREFIXES = ("py-v", "ts-v", "pi-v")
JsonObject = dict[str, Any]
Reader = Callable[[str], JsonObject]


class ReleasePolicyError(Exception):
    """The release did not satisfy the policy, including unavailable evidence."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ReleasePolicyError("GitHub API redirected; refusing to forward credentials")


class GitHubReader:
    def __init__(self, token: str) -> None:
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def __call__(self, path: str) -> JsonObject:
        if not path.startswith("/") or path.startswith("//"):
            raise ReleasePolicyError("Invalid GitHub API path")
        request = urllib.request.Request(
            API_ROOT + path,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ReleasePolicyError(f"GitHub API request failed (HTTP {exc.code})") from exc
        except (OSError, ValueError) as exc:
            raise ReleasePolicyError("GitHub API evidence is unavailable or invalid") from exc
        if not isinstance(result, dict):
            raise ReleasePolicyError("GitHub API returned an unexpected response")
        return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleasePolicyError(message)


def valid_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_PATTERN.fullmatch(value) is not None


def validate_ref(repository: str, event: str, ref: str, sha: str, tag_prefix: str) -> bool:
    require(repository == REPOSITORY, "Release must run in the expected repository")
    require(tag_prefix in TAG_PREFIXES, "Unknown package tag prefix")
    require(valid_sha(sha), "Release SHA must be a full commit SHA")
    require(event in ("push", "workflow_dispatch"), "Release event is not allowed")
    if ref == "refs/heads/main":
        require(event == "workflow_dispatch", "Only manual releases may target main directly")
        return False
    pattern = rf"refs/tags/{re.escape(tag_prefix)}[0-9][A-Za-z0-9.+_-]*\Z"
    require(re.fullmatch(pattern, ref) is not None, "Release ref is not main or a package release tag")
    return True


def resolve_tag(read: Reader, ref: str) -> str:
    tag_name = urllib.parse.quote(ref.removeprefix("refs/tags/"), safe="")
    value = read(f"/git/ref/tags/{tag_name}")
    require(value.get("ref") == ref, "GitHub returned a different release ref")
    obj = value.get("object", {})
    for _ in range(8):
        require(isinstance(obj, dict) and valid_sha(obj.get("sha")), "Invalid release tag target")
        if obj.get("type") == "commit":
            return obj["sha"]
        require(obj.get("type") == "tag", "Release tag does not resolve to a commit")
        obj = read(f"/git/tags/{obj['sha']}").get("object", {})
    raise ReleasePolicyError("Release tag indirection exceeds the policy limit")


def latest_ci_run(read: Reader, sha: str) -> JsonObject:
    query = urllib.parse.urlencode({"event": "push", "branch": "main", "head_sha": sha, "per_page": 100})
    data = read(f"/actions/workflows/ci.yml/runs?{query}")
    runs = data.get("workflow_runs")
    require(isinstance(runs, list) and bool(runs), "No trusted main CI run exists for the release SHA")
    # The API filter is not the trust decision: independently check each field.
    candidates = [
        run for run in runs
        if isinstance(run, dict)
        and run.get("head_sha") == sha
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("path") == ".github/workflows/ci.yml"
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("head_repository", {}).get("full_name") == REPOSITORY
        and isinstance(run.get("id"), int)
    ]
    require(bool(candidates), "No trusted main CI run matches the release SHA and workflow")
    run = max(candidates, key=lambda item: item["id"])
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "Latest trusted CI run is not successfully completed")
    require(isinstance(run.get("run_attempt"), int) and run["run_attempt"] > 0,
            "Trusted CI run has no valid attempt")
    require(isinstance(run.get("check_suite_id"), int), "Trusted CI run has no check suite")
    return run


def validate_jobs(read: Reader, run: JsonObject, sha: str) -> None:
    jobs: list[JsonObject] = []
    run_id, attempt = run["id"], run["run_attempt"]
    for page in range(1, 101):
        data = read(f"/actions/runs/{run_id}/jobs?filter=latest&per_page=100&page={page}")
        batch = data.get("jobs")
        require(isinstance(batch, list), "GitHub returned invalid CI jobs")
        jobs.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ReleasePolicyError("CI job pagination exceeds the policy limit")
    for name in REQUIRED_JOBS:
        matches = [job for job in jobs if isinstance(job, dict) and job.get("name") == name]
        require(len(matches) == 1, f"CI must have exactly one required job: {name}")
        job = matches[0]
        require(job.get("run_id") == run_id
                and isinstance(job.get("run_attempt"), int)
                and 0 < job["run_attempt"] <= attempt,
                f"Required job is from a different CI run or attempt: {name}")
        require(job.get("head_sha") == sha, f"Required job targets a different commit: {name}")
        require(job.get("status") == "completed" and job.get("conclusion") == "success",
                f"Required CI job did not succeed: {name}")
        check_url = job.get("check_run_url", "")
        require(isinstance(check_url, str), f"Required job has no check run: {name}")
        match = re.fullmatch(re.escape(API_ROOT) + r"/check-runs/([0-9]+)", check_url)
        require(match is not None, f"Required job has an untrusted check URL: {name}")
        check = read(f"/check-runs/{match[1]}")
        app = check.get("app", {})
        require(app.get("id") == ACTIONS_APP_ID and app.get("slug") == "github-actions",
                f"Required check was not created by GitHub Actions: {name}")
        require(check.get("head_sha") == sha and check.get("name") == name,
                f"Required check does not match the release job: {name}")
        require(check.get("check_suite", {}).get("id") == run["check_suite_id"],
                f"Required check belongs to a different CI suite: {name}")
        require(check.get("status") == "completed" and check.get("conclusion") == "success",
                f"Required GitHub Actions check did not succeed: {name}")


def authorize_release(read: Reader, *, repository: str, event: str, ref: str,
                      sha: str, tag_prefix: str) -> JsonObject:
    is_tag = validate_ref(repository, event, ref, sha, tag_prefix)
    if is_tag:
        require(resolve_tag(read, ref) == sha, "Release tag no longer resolves to the event commit")
    main = read("/branches/main")
    require(main.get("name") == "main" and main.get("protected") is True,
            "Release policy requires protected main")
    main_sha = main.get("commit", {}).get("sha")
    require(valid_sha(main_sha), "Protected main has no valid commit SHA")
    comparison = read(f"/compare/{sha}...{main_sha}")
    require(comparison.get("status") in ("ahead", "identical")
            and comparison.get("merge_base_commit", {}).get("sha") == sha,
            "Release commit is not part of protected main history")
    run = latest_ci_run(read, sha)
    validate_jobs(read, run, sha)
    return {"sha": sha, "ci_run_id": run["id"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag-prefix", choices=TAG_PREFIXES, required=True)
    args = parser.parse_args()
    try:
        token = os.environ.get("GH_TOKEN", "")
        require(bool(token), "GitHub read token is required")
        result = authorize_release(
            GitHubReader(token), repository=os.environ.get("GITHUB_REPOSITORY", ""),
            event=os.environ.get("GITHUB_EVENT_NAME", ""), ref=os.environ.get("GITHUB_REF", ""),
            sha=os.environ.get("GITHUB_SHA", ""), tag_prefix=args.tag_prefix,
        )
        output = os.environ.get("GITHUB_OUTPUT")
        require(bool(output), "GitHub output file is required")
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"sha={result['sha']}\nci-run-id={result['ci_run_id']}\n")
        print(f"Authorized release {result['sha']} using successful CI run {result['ci_run_id']}")
        return 0
    except ReleasePolicyError as exc:
        print(f"Release denied: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
