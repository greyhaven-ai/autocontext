"""bun.lock sync guard tests on synthetic lockfiles; no network and no bun."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from check_bun_lock_sync import (
    Drift,
    bun_versions,
    compare_versions,
    load_bun_lock,
    main,
    npm_versions,
)

INTEGRITY = "sha512-" + "A" * 86 + "=="


def npm_lock(installed: dict[str, str]) -> dict:
    """A lockfileVersion 3 package-lock.json: the root project plus install path -> version."""
    packages: dict = {
        "": {"name": "autoctx", "version": "0.19.0", "license": "Apache-2.0"}
    }
    for path, version in installed.items():
        name = path.rpartition("node_modules/")[2]
        packages[path] = {
            "version": version,
            "resolved": f"https://registry.npmjs.org/{name}/-/{name.rpartition('/')[2]}-{version}.tgz",
            "integrity": INTEGRITY,
            "license": "MIT",
        }
    return {
        "name": "autoctx",
        "version": "0.19.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": packages,
    }


def bun_lock(entries: dict[str, str]) -> dict:
    """A parsed lockfileVersion 3 bun.lock: lock key -> "name@version" spec."""
    return {
        "lockfileVersion": 3,
        "configVersion": 0,
        "workspaces": {"": {"name": "autoctx"}},
        "packages": {key: [spec, "", {}, INTEGRITY] for key, spec in entries.items()},
    }


def bun_lock_text(entries: dict[str, str]) -> str:
    """bun.lock as bun writes it: trailing commas, and a blank line between packages."""
    packages = "\n\n".join(
        f'    "{key}": ["{spec}", "", {{}}, "{INTEGRITY}"],'
        for key, spec in entries.items()
    )
    return (
        '{\n  "lockfileVersion": 3,\n  "configVersion": 0,\n  "workspaces": {\n    "": {\n'
        '      "name": "autoctx",\n      "optionalPeers": [\n        "openai",\n      ],\n    },\n  },\n'
        f'  "packages": {{\n{packages}\n  }}\n}}\n'
    )


class NpmVersionsTests(unittest.TestCase):
    def test_every_install_path_counts_toward_its_package(self):
        lock = npm_lock(
            {
                "node_modules/esbuild": "0.28.2",
                "node_modules/@secure-exec/core": "0.1.0",
                "node_modules/@secure-exec/core/node_modules/esbuild": "0.27.7",
                "node_modules/@secure-exec/core/node_modules/@esbuild/linux-x64": "0.27.7",
                "node_modules/@secure-exec/node": "0.1.0",
                "node_modules/@secure-exec/node/node_modules/esbuild": "0.27.7",
            }
        )
        self.assertEqual(
            npm_versions(lock),
            {
                "esbuild": {"0.28.2", "0.27.7"},
                "@secure-exec/core": {"0.1.0"},
                "@secure-exec/node": {"0.1.0"},
                "@esbuild/linux-x64": {"0.27.7"},
            },
        )

    def test_npm_alias_counts_under_the_real_package_name(self):
        lock = npm_lock(
            {
                "node_modules/string-width": "5.1.2",
                "node_modules/string-width-cjs": "4.2.3",
            }
        )
        lock["packages"]["node_modules/string-width-cjs"].update(
            name="string-width",
            resolved="https://registry.npmjs.org/string-width/-/string-width-4.2.3.tgz",
        )
        self.assertEqual(npm_versions(lock), {"string-width": {"4.2.3", "5.1.2"}})

    def test_entries_without_a_version_are_rejected(self):
        no_version = npm_lock({"node_modules/openai": "7.10.0"})
        del no_version["packages"]["node_modules/openai"]["version"]
        cases = {
            "no packages table": {"lockfileVersion": 3},
            "entry without version": no_version,
        }
        for label, lock in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                npm_versions(lock)


class BunVersionsTests(unittest.TestCase):
    def test_versions_come_from_specs_not_lock_keys(self):
        lock = bun_lock(
            {
                "js-yaml": "js-yaml@5.2.3",
                "json-schema-to-typescript/js-yaml": "js-yaml@4.3.1",
                "@secure-exec/core/esbuild/@esbuild/linux-x64": "@esbuild/linux-x64@0.27.7",
                "string-width-cjs": "string-width@4.2.3",
            }
        )
        self.assertEqual(
            bun_versions(lock),
            {
                "js-yaml": {"5.2.3", "4.3.1"},
                "@esbuild/linux-x64": {"0.27.7"},
                "string-width": {"4.2.3"},
            },
        )

    def test_entries_without_a_name_and_version_are_rejected(self):
        cases = {
            "no packages table": {"lockfileVersion": 3},
            "entry is not a list": {"packages": {"openai": "openai@7.10.0"}},
            "empty entry": {"packages": {"openai": []}},
            "spec without version": bun_lock({"openai": "openai"}),
            "spec with empty version": bun_lock({"openai": "openai@"}),
            "scoped spec without version": bun_lock(
                {"@modelcontextprotocol/sdk": "@modelcontextprotocol/sdk"}
            ),
        }
        for label, lock in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                bun_versions(lock)


class CompareVersionsTests(unittest.TestCase):
    def test_same_version_sets_agree(self):
        npm = {"js-yaml": {"4.3.1", "5.2.3"}, "openai": {"7.10.0"}}
        bun = {"js-yaml": {"5.2.3", "4.3.1"}, "openai": {"7.10.0"}}
        self.assertEqual(compare_versions(npm, bun), [])

    def test_version_mismatch_is_drift(self):
        npm = {"openai": {"7.10.0"}, "zod": {"3.25.76"}}
        bun = {"openai": {"7.13.0"}, "zod": {"3.25.76"}}
        self.assertEqual(
            compare_versions(npm, bun), [Drift("openai", ("7.10.0",), ("7.13.0",))]
        )

    def test_package_locked_on_only_one_side_is_drift(self):
        # vitest 5 moved from rollup to rolldown; a stale bun.lock keeps rollup.
        npm = {"rolldown": {"1.2.9"}}
        bun = {"rollup": {"4.59.0"}}
        self.assertEqual(
            compare_versions(npm, bun),
            [
                Drift("rolldown", ("1.2.9",), ()),
                Drift("rollup", (), ("4.59.0",)),
            ],
        )

    def test_differing_nested_copy_is_drift(self):
        npm = {"esbuild": {"0.28.2", "0.27.7"}}
        bun = {"esbuild": {"0.28.2", "0.27.3"}}
        self.assertEqual(
            compare_versions(npm, bun),
            [Drift("esbuild", ("0.27.7", "0.28.2"), ("0.27.3", "0.28.2"))],
        )


class LoadBunLockTests(unittest.TestCase):
    def test_trailing_commas_are_accepted(self):
        self.assertEqual(
            load_bun_lock(bun_lock_text({"openai": "openai@7.10.0"})),
            {
                "lockfileVersion": 3,
                "configVersion": 0,
                "workspaces": {"": {"name": "autoctx", "optionalPeers": ["openai"]}},
                "packages": {"openai": ["openai@7.10.0", "", {}, INTEGRITY]},
            },
        )


class MainTests(unittest.TestCase):
    def run_main(self, files: dict[str, str]) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            for name, text in files.items():
                (Path(temp_dir) / name).write_text(text, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main(["--package-dir", temp_dir])
        return code, output.getvalue()

    def test_locks_that_differ_only_in_hoisting_pass(self):
        # npm hoists js-yaml 4.3.1 and nests 5.2.3 under the parser; bun does the reverse.
        code, _ = self.run_main(
            {
                "package-lock.json": json.dumps(
                    npm_lock(
                        {
                            "node_modules/@apidevtools/json-schema-ref-parser": "16.0.1",
                            "node_modules/@apidevtools/json-schema-ref-parser/node_modules/js-yaml": "5.2.3",
                            "node_modules/js-yaml": "4.3.1",
                            "node_modules/json-schema-to-typescript": "15.0.4",
                        }
                    ),
                    indent=2,
                ),
                "bun.lock": bun_lock_text(
                    {
                        "@apidevtools/json-schema-ref-parser": "@apidevtools/json-schema-ref-parser@16.0.1",
                        "js-yaml": "js-yaml@5.2.3",
                        "json-schema-to-typescript": "json-schema-to-typescript@15.0.4",
                        "json-schema-to-typescript/js-yaml": "js-yaml@4.3.1",
                    }
                ),
            }
        )
        self.assertEqual(code, 0)

    def test_drift_fails_and_reports_both_versions(self):
        code, output = self.run_main(
            {
                "package-lock.json": json.dumps(
                    npm_lock({"node_modules/openai": "7.10.0"})
                ),
                "bun.lock": bun_lock_text({"openai": "openai@7.13.0"}),
            }
        )
        self.assertEqual(code, 1)
        lines = output.splitlines()
        self.assertTrue(
            any(
                "openai" in line and "7.10.0" in line and "7.13.0" in line
                for line in lines
            ),
            output,
        )

    def test_missing_or_unreadable_locks_are_errors_not_drift(self):
        npm_text = json.dumps(npm_lock({"node_modules/openai": "7.10.0"}))
        bun_text = bun_lock_text({"openai": "openai@7.10.0"})
        cases = {
            "bun.lock missing": {"package-lock.json": npm_text},
            "package-lock.json missing": {"bun.lock": bun_text},
            "package-lock.json truncated": {
                "package-lock.json": npm_text[:-1],
                "bun.lock": bun_text,
            },
            "bun.lock entry malformed": {
                "package-lock.json": npm_text,
                "bun.lock": bun_lock_text({"openai": "openai"}),
            },
        }
        for label, files in cases.items():
            with self.subTest(label):
                code, _ = self.run_main(files)
                self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
