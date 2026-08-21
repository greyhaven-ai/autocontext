from __future__ import annotations

import copy
import math

import pytest
from pydantic import ValidationError

from autocontext.kernel_evolution import (
    ProfileEvidenceEnvelope,
    build_profile_evidence_envelope,
    canonical_authority_digest,
    verify_profile_evidence_envelope,
)

_KEY_ID = "test-profile-evidence-key-v1"
_SECRET = b"test-only-profile-evidence-secret-material"


def _profile() -> dict[str, object]:
    return {
        "schema_version": "autocontext.kernel-h100-profile-evidence/v3",
        "champion": {"artifact_digest": "sha256:" + "a" * 64},
        "promotions": 1,
        "all_holdout_correctness_passed": True,
    }


def test_profile_evidence_envelope_authenticates_every_nested_field() -> None:
    envelope = build_profile_evidence_envelope(
        _profile(),
        signing_key_id=_KEY_ID,
        signing_secret=_SECRET,
    )
    assert verify_profile_evidence_envelope(
        envelope.model_dump(mode="json"),
        trusted_key_id=_KEY_ID,
        trusted_secret=_SECRET,
    ) == envelope

    forged = envelope.model_dump(mode="json")
    forged["profile"]["champion"]["artifact_digest"] = "sha256:" + "b" * 64
    forged["content_digest"] = canonical_authority_digest(forged["profile"])
    with pytest.raises(ValueError, match="authentication tag"):
        verify_profile_evidence_envelope(
            forged,
            trusted_key_id=_KEY_ID,
            trusted_secret=_SECRET,
        )

    with pytest.raises(ValueError, match="authentication tag"):
        verify_profile_evidence_envelope(
            envelope,
            trusted_key_id=_KEY_ID,
            trusted_secret=b"different-profile-evidence-secret-material",
        )


def test_profile_evidence_envelope_rejects_wrong_key_tag_and_shape() -> None:
    envelope = build_profile_evidence_envelope(
        _profile(),
        signing_key_id=_KEY_ID,
        signing_secret=_SECRET,
    )
    with pytest.raises(ValueError, match="authentication key"):
        verify_profile_evidence_envelope(
            envelope,
            trusted_key_id="different-profile-evidence-key",
            trusted_secret=_SECRET,
        )

    forged_tag = envelope.model_dump(mode="json")
    forged_tag["authentication"]["tag"] = "hmac-sha256:" + "0" * 64
    with pytest.raises(ValueError, match="authentication tag"):
        verify_profile_evidence_envelope(
            forged_tag,
            trusted_key_id=_KEY_ID,
            trusted_secret=_SECRET,
        )

    extra = envelope.model_dump(mode="json")
    extra["unsigned_summary"] = {"promotions": 999}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProfileEvidenceEnvelope.model_validate(extra)

    raw = envelope.model_dump_json()
    duplicate = raw.replace('"content_digest":', '"content_digest":"sha256:' + "f" * 64 + '","content_digest":')
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        verify_profile_evidence_envelope(
            duplicate,
            trusted_key_id=_KEY_ID,
            trusted_secret=_SECRET,
        )
    malformed_number = raw.replace('"promotions":1', '"promotions":NaN')
    with pytest.raises(ValueError, match="invalid JSON constant"):
        verify_profile_evidence_envelope(
            malformed_number,
            trusted_key_id=_KEY_ID,
            trusted_secret=_SECRET,
        )


@pytest.mark.parametrize(
    "invalid",
    (
        {"schema_version": "valid/v1", "value": math.nan},
        {"schema_version": "valid/v1", "value": {"not", "json"}},
        {"schema_version": "bad\nschema", "value": 1},
    ),
)
def test_profile_evidence_envelope_rejects_noncanonical_json(invalid: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        build_profile_evidence_envelope(
            copy.deepcopy(invalid),
            signing_key_id=_KEY_ID,
            signing_secret=_SECRET,
        )
