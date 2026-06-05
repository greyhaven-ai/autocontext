"""Tests for the domain-agnostic training-record augmentation seam (PR5a)."""

from __future__ import annotations

import pytest

from autocontext.training.autoresearch.augment import apply_augmentation, resolve_augmenter


def test_resolve_augmenter_empty_spec_returns_none() -> None:
    assert resolve_augmenter("") is None
    assert resolve_augmenter("   ") is None


@pytest.mark.parametrize("spec", ["nocolon", "a:b:c", ":func", "module:", "  :  "])
def test_resolve_augmenter_malformed_spec_raises(spec: str) -> None:
    with pytest.raises(ValueError, match="package.module:function"):
        resolve_augmenter(spec)


def test_resolve_augmenter_unimportable_module_raises() -> None:
    with pytest.raises(ValueError, match="could not import augmenter module"):
        resolve_augmenter("autocontext._definitely_not_a_module:fn")


def test_resolve_augmenter_missing_attribute_raises() -> None:
    with pytest.raises(ValueError, match="did not resolve to a callable"):
        resolve_augmenter("json:not_a_real_attr")


def test_resolve_augmenter_non_callable_attribute_raises() -> None:
    with pytest.raises(ValueError, match="did not resolve to a callable"):
        resolve_augmenter("math:pi")  # math.pi is a float, not callable


def test_resolve_augmenter_valid_spec_returns_callable() -> None:
    fn = resolve_augmenter("json:dumps")
    assert callable(fn)


def test_apply_augmentation_none_is_identity_copy() -> None:
    records = [{"strategy": {"a": 1}, "score": 0.5}]
    out = apply_augmentation(records, None)
    assert out == records
    assert out is not records  # returns a copy, not the same list


def test_apply_augmentation_expands_records() -> None:
    records = [{"strategy": {"a": 1}, "score": 0.5}]

    def double(recs: list[dict]) -> list[dict]:
        return recs + [{**r, "augmented": True} for r in recs]

    out = apply_augmentation(records, double)
    assert len(out) == 2
    assert out[1]["augmented"] is True


@pytest.mark.parametrize("bad", [[], "not-a-list", (), None])
def test_apply_augmentation_rejects_empty_or_non_list_output(bad: object) -> None:
    with pytest.raises(ValueError, match="non-empty list of records"):
        apply_augmentation([{"strategy": {}, "score": 0.0}], lambda _recs: bad)  # type: ignore[arg-type,return-value]


def test_apply_augmentation_rejects_non_dict_items() -> None:
    with pytest.raises(ValueError, match="list of record dicts"):
        apply_augmentation([{"strategy": {}, "score": 0.0}], lambda _recs: ["not-a-dict"])  # type: ignore[arg-type,list-item]
