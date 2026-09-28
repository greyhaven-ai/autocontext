#!/usr/bin/env python3
"""Fail when a pull request newly locks an npm version younger than the release-age policy.

CI installs with `npm ci`, which does not enforce NPM_CONFIG_MIN_RELEASE_AGE
(only `npm install` does). Dependabot's npm version updates pass their cooldown
to npm when they regenerate a lockfile, so they skip versions inside it. This
check is the backstop for what that does not cover: lockfile changes Dependabot
does not make, Dependabot security updates (which run with no cooldown),
versions a cooldown shorter than this policy lets through, and regressions in
Dependabot's undocumented transitive gate. It runs alongside the other CI jobs,
so it keeps young versions out of main, not out of the pull request's own
installs.

For ts/package-lock.json and pi/package-lock.json, it compares the pull request
head with its merge base and collects every (name, version) pair the head newly
locks from registry.npmjs.org. It looks up each version's publish time in the
registry and fails if any is younger than NPM_CONFIG_MIN_RELEASE_AGE days, read
from the environment so the policy has one source of truth. autoctx, which this
repository publishes, is exempt. Newly locked packages from anywhere else (git,
a file, another host, or an entry without a `resolved` URL) cannot be dated
here; they are listed with a warning but do not fail the check.

Versions clear the policy as they age, so the remedy is to re-run the job after
the time the failure prints. For an urgent security fix, a maintainer can apply
the npm-release-age-override label instead: the check still lists the young
versions and warns, then passes. The label is read from the event payload, and
a re-run reuses the payload of the run it repeats, so after adding the label
start a new run: push a commit, close and reopen the pull request, or comment
`@dependabot recreate` on a Dependabot pull request. The label keeps applying to
later runs on that pull request.

To check a branch locally (Python 3.11 or newer):

    cd autocontext
    NPM_CONFIG_MIN_RELEASE_AGE=7 uv run python ../scripts/check_npm_release_age.py --base origin/main --head HEAD
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCKFILES = ("ts/package-lock.json", "pi/package-lock.json")
AGE_VARIABLE = "NPM_CONFIG_MIN_RELEASE_AGE"
REGISTRY = "https://registry.npmjs.org/"
# Pi must be able to consume an autoctx release from this repository as soon as
# it is published; publish-pi-autocontext.yml exempts it the same way through
# NPM_CONFIG_MIN_RELEASE_AGE_EXCLUDE.
EXEMPT_PACKAGES = frozenset({"autoctx"})
OVERRIDE_LABEL = "npm-release-age-override"
Lockfile = Mapping[str, Any]
Pair = tuple[str, str]
PublishTimes = Callable[[str], Mapping[str, Any]]


class CheckError(Exception):
    """The check could not establish what a pull request locks, or how old it is."""


@dataclass(frozen=True, slots=True, order=True)
class Unchecked:
    """A package installed from somewhere other than registry.npmjs.org, so it has no publish time to check."""

    name: str
    version: str
    source: str


@dataclass(frozen=True, slots=True)
class YoungVersion:
    name: str
    version: str
    published: datetime
    clears: datetime


def identify(path: str, entry: Mapping[str, Any]) -> Pair:
    # An alias ("x": "npm:y@1") installs under its own path but records the real name.
    name = entry.get("name") or path.rpartition("node_modules/")[2]
    version = entry.get("version")
    if not (isinstance(name, str) and name and isinstance(version, str) and version):
        raise CheckError(f"lockfile entry {path!r} has no package name or version")
    return name, version


def locked_entries(lockfile: Lockfile) -> tuple[set[Pair], set[Unchecked]]:
    """Split what a v2 or v3 lockfile installs into npm registry versions and packages from anywhere else."""
    packages = lockfile.get("packages")
    if not isinstance(packages, dict):
        raise CheckError("lockfile has no `packages` map; only lockfileVersion 2 and 3 are supported")
    registry, unchecked = set(), set()
    for path, entry in packages.items():
        if not isinstance(entry, dict) or entry.get("link"):
            continue
        resolved = entry.get("resolved")
        if isinstance(resolved, str) and resolved.startswith(REGISTRY):
            registry.add(identify(path, entry))
        # Skip the root and workspace folders (not under node_modules), and dependencies bundled in their
        # parent's tarball. Anything else came from git, a file, another host, or an unrecorded registry.
        elif "node_modules/" in path and not entry.get("inBundle"):
            source = resolved if isinstance(resolved, str) else "no resolved URL"
            unchecked.add(Unchecked(*identify(path, entry), source))
    return registry, unchecked


def newly_locked(base: Lockfile | None, head: Lockfile | None) -> set[Pair]:
    """Return registry versions locked at head but nowhere in base, except this repository's own package."""
    if head is None:
        return set()
    before = locked_entries(base)[0] if base is not None else set()
    return {pair for pair in locked_entries(head)[0] - before if pair[0] not in EXEMPT_PACKAGES}


def newly_unchecked(base: Lockfile | None, head: Lockfile | None) -> set[Unchecked]:
    """Return the packages head newly installs from somewhere other than registry.npmjs.org."""
    if head is None:
        return set()
    before = locked_entries(base)[1] if base is not None else set()
    return locked_entries(head)[1] - before


def young_versions(pairs: Iterable[Pair], publish_times: Mapping[str, Mapping[str, Any]], *,
                   now: datetime, min_age: timedelta) -> list[YoungVersion]:
    """Return the pairs published less than min_age before now, given each package's registry `time` map."""
    young = []
    for name, version in sorted(pairs):
        try:
            published = datetime.fromisoformat(publish_times[name][version])
        except (KeyError, TypeError, ValueError):
            raise CheckError(f"the npm registry has no readable publish time for {name}@{version}") from None
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        clears = published + min_age
        if now < clears:
            young.append(YoungVersion(name, version, published, clears))
    return young


def policy_age(raw: str | None) -> timedelta:
    try:
        days = float(raw or "")
    except ValueError:
        days = math.nan
    if not (math.isfinite(days) and days >= 0):
        raise CheckError(f"{AGE_VARIABLE} must be a non-negative number of days, not {raw!r}")
    return timedelta(days=days)


def git(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, encoding="utf-8")
    except subprocess.CalledProcessError as exc:
        raise CheckError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return result.stdout


def read_lockfile(rev: str, path: str) -> dict[str, Any] | None:
    if not git("ls-tree", rev, "--", path).strip():
        return None
    try:
        lockfile = json.loads(git("cat-file", "blob", f"{rev}:{path}"))
    except ValueError as exc:
        raise CheckError(f"{path} at {rev} is not valid JSON: {exc}") from exc
    if not isinstance(lockfile, dict):
        raise CheckError(f"{path} at {rev} is not a lockfile")
    return lockfile


def merge_base(base: str, head: str) -> str:
    try:
        return git("merge-base", base, head).strip()
    except CheckError as exc:
        raise CheckError(f"cannot find the merge base of {base} and {head} ({exc}); "
                         "in CI, check out the full history (actions/checkout fetch-depth: 0)") from exc


def registry_publish_times(name: str) -> Mapping[str, Any]:
    """Fetch the `time` map of a package's registry document, retrying transient failures."""
    url = REGISTRY + urllib.parse.quote(name, safe="@")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(3):
        if attempt:
            time.sleep(5 * attempt)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                document = json.load(response)
        except urllib.error.HTTPError as exc:
            error = f"the npm registry returned HTTP {exc.code} for {name}"
            if exc.code < 500 and exc.code != 429:
                break
        except (OSError, http.client.HTTPException, ValueError) as exc:
            error = f"cannot read {name} from the npm registry: {exc!r}"
        else:
            times = document.get("time") if isinstance(document, dict) else None
            if isinstance(times, dict):
                return times
            error = f"the npm registry returned no publish times for {name}"
            break
    raise CheckError(error)


def load_event(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CheckError(f"cannot read the GitHub event payload: {exc}") from exc
    return event if isinstance(event, dict) else {}


def override_requested(event: Mapping[str, Any]) -> bool:
    labels = (event.get("pull_request") or {}).get("labels") or []
    return any(isinstance(label, dict) and label.get("name") == OVERRIDE_LABEL for label in labels)


def utc(moment: datetime, *, round_up: bool = False) -> str:
    if round_up and moment.microsecond:
        moment += timedelta(microseconds=1_000_000 - moment.microsecond)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def specs(items: Iterable[YoungVersion | Unchecked]) -> str:
    return ", ".join(f"{item.name}@{item.version}" for item in items)


def annotate(level: str, message: str) -> None:
    """Repeat a message as a GitHub Actions annotation, so it shows on the run's page even when the job passes."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level} title=npm release age::{escaped}")


def main(argv: Sequence[str] | None = None, *, publish_times: PublishTimes = registry_publish_times,
         now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", help="base revision (default: the pull request base in GITHUB_EVENT_PATH)")
    parser.add_argument("--head", help="head revision (default: the pull request head in GITHUB_EVENT_PATH)")
    args = parser.parse_args(argv)
    try:
        min_age = policy_age(os.environ.get(AGE_VARIABLE))
        event = load_event(os.environ.get("GITHUB_EVENT_PATH"))
        pull_request = event.get("pull_request") or {}
        base = args.base or (pull_request.get("base") or {}).get("sha")
        head = args.head or (pull_request.get("head") or {}).get("sha")
        if not (base and head):
            raise CheckError("the GitHub event has no pull request; pass --base and --head")
        fork = merge_base(base, head)
        locks = {path: (read_lockfile(fork, path), read_lockfile(head, path)) for path in LOCKFILES}
        changes = {path: newly_locked(*pair) for path, pair in locks.items()}
        unchecked = {path: newly_unchecked(*pair) for path, pair in locks.items()}
        names = sorted({name for pairs in changes.values() for name, _ in pairs})
        with ThreadPoolExecutor(max_workers=8) as pool:
            times = dict(zip(names, pool.map(publish_times, names), strict=True))
        checked_at = now or datetime.now(UTC)
        young = {path: young_versions(pairs, times, now=checked_at, min_age=min_age) for path, pairs in changes.items()}
    except CheckError as exc:
        print(f"npm release-age check failed: {exc}", file=sys.stderr)
        return 1

    policy = f"the {min_age / timedelta(days=1):g}-day {AGE_VARIABLE}"
    print(f"Comparing the lockfiles at {head} with its merge base {fork}:")
    for path in LOCKFILES:
        pairs, found = changes[path], young[path]
        newly = plural(len(pairs), "newly locked registry package")
        if not pairs:
            print(f"{path}: no newly locked registry packages")
        elif not found:
            print(f"{path}: {newly}, none younger than {policy}")
        else:
            print(f"{path}: {newly}, {len(found)} younger than {policy}:")
            width = max(len(f"{item.name}@{item.version}") for item in found)
            for item in found:
                spec = f"{item.name}@{item.version}"
                print(f"  {spec:<{width}}  published {utc(item.published)}  clears {utc(item.clears, round_up=True)}")
        if unchecked[path]:
            print(f"{path}: {plural(len(unchecked[path]), 'newly locked package')} not from {REGISTRY}, so not age-checked:")
            for other in sorted(unchecked[path]):
                print(f"  {other.name}@{other.version}  {other.source}")
    elsewhere = sorted(other for others in unchecked.values() for other in others)
    if elsewhere:
        annotate("warning", f"Not age-checked, because they are not from {REGISTRY}: {specs(elsewhere)}")
    flagged = [item for found in young.values() for item in found]
    if not flagged:
        return 0
    last = utc(max(item.clears for item in flagged), round_up=True)
    if override_requested(event):
        print(f"Allowed by the {OVERRIDE_LABEL} label. The last of these versions clears {policy} at {last}.")
        annotate("warning", f"The {OVERRIDE_LABEL} label allowed versions younger than {policy}: {specs(flagged)}")
        return 0
    summary = f"{len(flagged)} newly locked {'version is' if len(flagged) == 1 else 'versions are'} younger than {policy}."
    print(summary)
    print(f"Re-run this job after {last}, when the last of them clears it.")
    print(f"For an urgent security fix, a maintainer can apply the {OVERRIDE_LABEL} label instead. A re-run reuses")
    print("the labels of the event that started it, so after adding the label, push a commit, close and reopen the")
    print("pull request, or comment `@dependabot recreate` on a Dependabot pull request.")
    annotate("error", f"{summary} Re-run this job after {last}.")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
