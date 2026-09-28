"""npm release-age check tests use synthetic lockfiles and registry data; no network."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import check_npm_release_age as checker
from check_npm_release_age import OVERRIDE_LABEL, CheckError, YoungVersion, newly_locked, young_versions

REGISTRY = "https://registry.npmjs.org"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
WEEK = timedelta(days=7)


def registry_entry(package, version, **fields):
    basename = package.rpartition("/")[2]
    return {
        "version": version, "resolved": f"{REGISTRY}/{package}/-/{basename}-{version}.tgz",
        "integrity": "sha512-" + "A" * 86 + "==", "license": "MIT",
    } | fields


def lockfile(packages):
    root = {"name": "fixture", "version": "1.0.0", "license": "MIT"}
    return {"name": "fixture", "version": "1.0.0", "lockfileVersion": 3, "requires": True, "packages": {"": root} | packages}


def publish_times(versions):
    """The `time` map of a registry packument: creation, last modification, then one entry per version."""
    return {"created": "2016-01-01T00:00:00.000Z", "modified": "2026-09-27T00:00:00.000Z"} | versions


def git(repo, *args):
    identity = {"GIT_AUTHOR_NAME": "Release Age", "GIT_AUTHOR_EMAIL": "release-age@example.invalid",
                "GIT_COMMITTER_NAME": "Release Age", "GIT_COMMITTER_EMAIL": "release-age@example.invalid"}
    env = os.environ | identity | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()


class NewlyLockedTests(unittest.TestCase):
    def test_version_bump_newly_locks_only_the_new_version(self):
        base = lockfile({"node_modules/ws": registry_entry("ws", "8.21.0")})
        head = lockfile({"node_modules/ws": registry_entry("ws", "8.21.3")})
        self.assertEqual(newly_locked(base, head), {("ws", "8.21.3")})

    def test_version_locked_at_another_path_in_base_is_not_new(self):
        base = lockfile({"node_modules/ajv/node_modules/fast-uri": registry_entry("fast-uri", "3.1.6")})
        head = lockfile({"node_modules/fast-uri": registry_entry("fast-uri", "3.1.6")})
        self.assertEqual(newly_locked(base, head), set())

    def test_scoped_and_nested_names_come_from_the_install_path(self):
        head = lockfile({
            "node_modules/@aws-sdk/core": registry_entry("@aws-sdk/core", "3.974.0"),
            "node_modules/@earendil-works/pi-ai/node_modules/@smithy/types": registry_entry("@smithy/types", "4.12.0"),
        })
        self.assertEqual(newly_locked(lockfile({}), head), {("@aws-sdk/core", "3.974.0"), ("@smithy/types", "4.12.0")})

    def test_alias_is_checked_under_the_name_it_resolves_to(self):
        head = lockfile({
            "node_modules/openai-v4": registry_entry("openai", "4.104.0", name="openai"),
            # An alias must not borrow the exemption of this repository's own package.
            "node_modules/autoctx": registry_entry("left-pad", "1.3.0", name="left-pad"),
        })
        self.assertEqual(newly_locked(lockfile({}), head), {("openai", "4.104.0"), ("left-pad", "1.3.0")})

    def test_only_registry_tarballs_are_checked(self):
        head = lockfile({
            "node_modules/ws": registry_entry("ws", "8.21.3"),
            "node_modules/local-sdk": {"resolved": "../sdk", "link": True},
            "../sdk": {"name": "local-sdk", "version": "0.1.0", "license": "MIT"},
            "node_modules/forked": {
                "version": "1.0.0", "resolved": "git+ssh://git@github.com/example/forked.git#" + "0" * 40, "license": "MIT",
            },
            "node_modules/vendored": {
                "version": "2.0.0", "resolved": "file:vendor/vendored-2.0.0.tgz",
                "integrity": "sha512-" + "B" * 86 + "==", "license": "MIT",
            },
            "node_modules/ws/node_modules/bundled": {"version": "1.0.0", "inBundle": True, "license": "MIT"},
        })
        self.assertEqual(newly_locked(lockfile({}), head), {("ws", "8.21.3")})

    def test_this_repositorys_autoctx_release_is_exempt(self):
        base = lockfile({"node_modules/autoctx": registry_entry("autoctx", "0.19.0")})
        head = lockfile({"node_modules/autoctx": registry_entry("autoctx", "0.20.0")})
        self.assertEqual(newly_locked(base, head), set())

    def test_added_lockfile_newly_locks_every_registry_package(self):
        head = lockfile({"node_modules/ws": registry_entry("ws", "8.21.3")})
        self.assertEqual(newly_locked(None, head), {("ws", "8.21.3")})

    def test_removed_lockfile_newly_locks_nothing(self):
        base = lockfile({"node_modules/ws": registry_entry("ws", "8.21.3")})
        self.assertEqual(newly_locked(base, None), set())

    def test_lockfile_without_a_packages_map_is_rejected(self):
        legacy = {"lockfileVersion": 1, "dependencies": {"ws": {"version": "8.21.3"}}}
        with self.assertRaises(CheckError):
            newly_locked(None, legacy)


class ReleaseAgeTests(unittest.TestCase):
    def test_version_younger_than_the_policy_reports_when_it_clears(self):
        registry = {"openai": publish_times({"7.10.0": "2026-08-01T09:00:00.000Z", "7.23.0": "2026-09-23T07:19:25.310Z"})}
        young = young_versions({("openai", "7.23.0")}, registry, now=NOW, min_age=WEEK)
        self.assertEqual(young, [YoungVersion(
            "openai", "7.23.0",
            published=datetime(2026, 9, 23, 7, 19, 25, 310000, tzinfo=UTC),
            clears=datetime(2026, 9, 30, 7, 19, 25, 310000, tzinfo=UTC),
        )])

    def test_version_published_exactly_the_policy_age_ago_passes(self):
        for stamp, expected in [("2026-09-21T12:00:00.000Z", []), ("2026-09-21T12:00:00.001Z", ["8.21.3"])]:
            with self.subTest(stamp=stamp):
                registry = {"ws": publish_times({"8.21.3": stamp})}
                young = young_versions({("ws", "8.21.3")}, registry, now=NOW, min_age=WEEK)
                self.assertEqual([item.version for item in young], expected)

    def test_unknown_or_unreadable_publish_time_fails_closed(self):
        for versions in [{"8.21.0": "2026-05-01T00:00:00.000Z"}, {"8.21.3": "last Tuesday"}]:
            with self.subTest(versions=versions):
                with self.assertRaises(CheckError):
                    young_versions({("ws", "8.21.3")}, {"ws": publish_times(versions)}, now=NOW, min_age=WEEK)


class PullRequestCheckTests(unittest.TestCase):
    """Run the CLI against a throwaway repository, as CI runs it against a pull request's history."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name) / "repo"
        self.event_path = Path(temp.name) / "event.json"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        self.write("ts/package-lock.json", {"node_modules/ws": registry_entry("ws", "8.21.0")})
        self.write("pi/package-lock.json", {"node_modules/autoctx": registry_entry("autoctx", "0.19.0")})
        self.fork = self.commit("main")
        git(self.repo, "checkout", "-q", "-b", "feature")
        self.registry = {
            "ws": publish_times({"8.21.0": "2026-06-01T08:30:00.000Z", "8.21.3": "2026-09-26T08:30:00.000Z"}),
            "undici": publish_times({"8.9.0": "2026-08-30T10:00:00.000Z", "8.10.0": "2026-09-27T21:05:44.051Z"}),
            "ms": publish_times({"2.1.3": "2020-12-10T20:34:21.543Z"}),
        }
        self.lookups = []

    def write(self, path, packages):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(lockfile(packages), indent=2) + "\n", encoding="utf-8")

    def commit(self, message):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def publish_times(self, name):
        self.lookups.append(name)
        if name not in self.registry:
            raise CheckError(f"the npm registry returned HTTP 404 for {name}")
        return self.registry[name]

    def pull_request_event(self, head, *, base=None, labels=()):
        labels = [{"id": index, "name": name, "color": "ededed", "default": False, "description": None}
                  for index, name in enumerate(labels, start=1)]
        return {"action": "synchronize", "number": 1426, "pull_request": {
            "number": 1426, "base": {"ref": "main", "sha": base or self.fork},
            "head": {"ref": "feature", "sha": head}, "labels": labels,
        }}

    def run_check(self, event, *args, min_age="7"):
        self.event_path.write_text(json.dumps(event), encoding="utf-8")
        env = {"GITHUB_EVENT_PATH": str(self.event_path), "NPM_CONFIG_MIN_RELEASE_AGE": min_age}
        output = io.StringIO()
        with (patch.dict(os.environ, env), patch.object(checker, "REPO_ROOT", self.repo),
              redirect_stdout(output), redirect_stderr(output)):
            code = checker.main(list(args), publish_times=self.publish_times, now=NOW)
        return code, output.getvalue()

    def test_young_versions_fail_with_publish_and_clear_times(self):
        self.write("ts/package-lock.json", {
            "node_modules/ws": registry_entry("ws", "8.21.3"), "node_modules/undici": registry_entry("undici", "8.10.0"),
        })
        code, output = self.run_check(self.pull_request_event(self.commit("bump ws, add undici")))
        self.assertEqual(code, 1, output)
        self.assertIn("ts/package-lock.json", output)
        ws = next(line for line in output.splitlines() if "ws@8.21.3" in line)
        self.assertIn("2026-09-26T08:30:00Z", ws)
        self.assertIn("2026-10-03T08:30:00Z", ws)
        undici = next(line for line in output.splitlines() if "undici@8.10.0" in line)
        self.assertIn("2026-09-27T21:05:44Z", undici)
        # Rounded up, so re-running at the printed time is never early.
        self.assertIn("2026-10-04T21:05:45Z", undici)
        [rerun] = [line for line in output.splitlines() if "Re-run" in line]
        self.assertIn("2026-10-04T21:05:45Z", rerun)

    def test_threshold_is_read_from_npm_config_min_release_age(self):
        self.write("ts/package-lock.json", {"node_modules/ws": registry_entry("ws", "8.21.3")})
        event = self.pull_request_event(self.commit("bump ws"))
        self.assertEqual(self.run_check(event, min_age="2")[0], 0)
        self.assertEqual(self.run_check(event, min_age="7")[0], 1)

    def test_unusable_min_release_age_fails_the_check(self):
        self.write("ts/package-lock.json", {
            "node_modules/ws": registry_entry("ws", "8.21.0"), "node_modules/ms": registry_entry("ms", "2.1.3"),
        })
        event = self.pull_request_event(self.commit("add ms"))
        for value in ["", "seven", "-1", "nan", "inf"]:
            with self.subTest(value=value):
                code, output = self.run_check(event, min_age=value)
                self.assertEqual(code, 1, output)
                self.assertIn("NPM_CONFIG_MIN_RELEASE_AGE", output)

    def test_override_label_passes_but_still_lists_young_versions(self):
        self.write("ts/package-lock.json", {"node_modules/ws": registry_entry("ws", "8.21.3")})
        head = self.commit("security fix")
        code, output = self.run_check(self.pull_request_event(head, labels=["dependencies", OVERRIDE_LABEL]))
        self.assertEqual(code, 0, output)
        self.assertIn("ws@8.21.3", output)
        self.assertIn(OVERRIDE_LABEL, output)
        code, output = self.run_check(self.pull_request_event(head, labels=["dependencies", "security"]))
        self.assertEqual(code, 1, output)

    def test_only_versions_new_since_the_merge_base_are_checked(self):
        # Main locked ws 8.21.3 early under the override, then moved on to 8.21.4.
        # The feature branch forked in between and still has 8.21.3.
        git(self.repo, "checkout", "-q", "main")
        self.write("ts/package-lock.json", {"node_modules/ws": registry_entry("ws", "8.21.3")})
        self.commit("main: security fix under the override")
        git(self.repo, "checkout", "-q", "-b", "stale-feature")
        self.write("ts/package-lock.json", {
            "node_modules/ws": registry_entry("ws", "8.21.3"), "node_modules/ms": registry_entry("ms", "2.1.3"),
        })
        head = self.commit("feature: add ms")
        git(self.repo, "checkout", "-q", "main")
        self.write("ts/package-lock.json", {"node_modules/ws": registry_entry("ws", "8.21.4")})
        base = self.commit("main: ws 8.21.4")
        code, output = self.run_check(self.pull_request_event(head, base=base))
        self.assertEqual(code, 0, output)
        self.assertEqual(self.lookups, ["ms"])

    def test_pull_request_without_lockfile_changes_skips_the_registry(self):
        (self.repo / "README.md").write_text("docs\n", encoding="utf-8")
        code, output = self.run_check(self.pull_request_event(self.commit("docs only")))
        self.assertEqual(code, 0, output)
        self.assertEqual(self.lookups, [])

    def test_registry_failure_fails_the_check(self):
        self.write("ts/package-lock.json", {
            "node_modules/ws": registry_entry("ws", "8.21.0"), "node_modules/left-pad": registry_entry("left-pad", "1.3.0"),
        })
        code, output = self.run_check(self.pull_request_event(self.commit("add left-pad")))
        self.assertEqual(code, 1, output)
        self.assertIn("left-pad", output)

    def test_unreadable_lockfile_fails_the_check(self):
        (self.repo / "ts/package-lock.json").write_text("<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> main\n", encoding="utf-8")
        code, output = self.run_check(self.pull_request_event(self.commit("unresolved conflict")))
        self.assertEqual(code, 1, output)
        self.assertIn("ts/package-lock.json", output)

    def test_revisions_come_from_arguments_when_the_event_has_no_pull_request(self):
        self.write("ts/package-lock.json", {"node_modules/ws": registry_entry("ws", "8.21.3")})
        head = self.commit("bump ws")
        push = {"ref": "refs/heads/feature", "before": self.fork, "after": head}
        code, output = self.run_check(push)
        self.assertEqual(code, 1, output)
        self.assertIn("--base", output)
        self.assertEqual(self.lookups, [])
        code, output = self.run_check(push, "--base", self.fork, "--head", head)
        self.assertEqual(code, 1, output)
        self.assertIn("ws@8.21.3", output)


if __name__ == "__main__":
    unittest.main()
