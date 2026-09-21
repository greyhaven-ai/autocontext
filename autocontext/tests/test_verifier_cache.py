"""Content-fingerprint evaluation cache (AC-902)."""

from __future__ import annotations

from autocontext.execution.verifier_cache import CachedVerdict, EvaluationCache, content_fingerprint


class TestContentFingerprint:
    def test_stable_and_content_sensitive(self) -> None:
        assert content_fingerprint("theorem foo") == content_fingerprint("theorem foo")
        assert content_fingerprint("theorem foo") != content_fingerprint("theorem bar")

    def test_salt_changes_fingerprint(self) -> None:
        assert content_fingerprint("x") != content_fingerprint("x", salt="mathlib-4.9")


class TestEvaluationCache:
    def _verdict(self, score: float, passed: bool) -> CachedVerdict:
        return CachedVerdict(score=score, reasoning="r", dimension_scores={}, passed=passed)

    def test_put_get_round_trip_and_stats(self) -> None:
        cache = EvaluationCache()
        fp = content_fingerprint("artifact")
        assert cache.get(fp) is None
        cache.put(fp, self._verdict(0.4, passed=False))
        cached = cache.get(fp)
        assert cached is not None and cached.score == 0.4
        stats = cache.stats()
        assert stats == {"hits": 1, "misses": 1, "entries": 1}

    def test_unchanged_failure_only_for_failed_verdicts(self) -> None:
        cache = EvaluationCache()
        fp_fail = content_fingerprint("bad")
        fp_pass = content_fingerprint("good")
        cache.put(fp_fail, self._verdict(0.2, passed=False))
        cache.put(fp_pass, self._verdict(0.95, passed=True))
        assert cache.unchanged_failure(fp_fail) is True
        assert cache.unchanged_failure(fp_pass) is False
        assert cache.unchanged_failure(content_fingerprint("unseen")) is False


def test_versioned_judge_verdict_requires_matching_expected_identity():
    cache = EvaluationCache()
    verdict = CachedVerdict(score=0.9, reasoning="ok", dimension_scores={}, passed=True,
                            evaluator_epoch="old", evaluator_spec="immutable-manifest")
    cache.put("same-artifact", verdict)
    assert cache.get("same-artifact") is None
    assert cache.get("same-artifact", expected_evaluator_epoch="new") is None
    assert cache.get("same-artifact", expected_evaluator_epoch="old") is verdict
    assert cache.stats() == {"hits": 1, "misses": 2, "entries": 1}


def test_expected_identity_rejects_legacy_or_unknown_cached_evidence():
    cache = EvaluationCache()
    cache.put("legacy", CachedVerdict(score=0.9, reasoning="old", dimension_scores={}, passed=True,
                                      evaluator_epoch="legacy-epoch"))
    cache.put("unknown", CachedVerdict(score=0.9, reasoning="old", dimension_scores={}, passed=True))
    assert cache.get("legacy", expected_evaluator_epoch="current-spec") is None
    assert cache.get("unknown", expected_evaluator_epoch="current-spec") is None
