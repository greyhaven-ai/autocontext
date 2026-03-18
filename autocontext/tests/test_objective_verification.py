"""Tests for AC-282: generic objective verification harness.

Covers: GroundTruthItem, KeywordMatchOracle, OracleResult,
OracleComparison, compare_oracle_vs_rubric.

Tests use multiple domains (drug interactions, math proofs, factual claims)
to prove the harness is domain-agnostic.
"""

from __future__ import annotations

import re

# ===========================================================================
# GroundTruthItem
# ===========================================================================


class TestGroundTruthItem:
    def test_construction(self) -> None:
        from autocontext.execution.objective_verification import GroundTruthItem

        item = GroundTruthItem(
            item_id="interaction-1",
            description="Warfarin + Aspirin bleeding risk",
            match_keywords=[["warfarin", "coumadin"], ["aspirin"]],
            weight="high",
            category="drug_interaction",
        )
        assert item.item_id == "interaction-1"
        assert item.weight == "high"
        assert len(item.match_keywords) == 2

    def test_roundtrip(self) -> None:
        from autocontext.execution.objective_verification import GroundTruthItem

        item = GroundTruthItem(
            item_id="step-1",
            description="Proof step: apply modus ponens",
            match_keywords=[["modus ponens"]],
            weight="moderate",
            category="proof_step",
        )
        d = item.to_dict()
        restored = GroundTruthItem.from_dict(d)
        assert restored.item_id == "step-1"
        assert restored.match_keywords == [["modus ponens"]]


# ===========================================================================
# KeywordMatchOracle — drug interaction domain
# ===========================================================================


class TestOracleDrugInteractions:
    def _drug_oracle(self):  # noqa: ANN202
        from autocontext.execution.objective_verification import (
            GroundTruthItem,
            KeywordMatchOracle,
        )

        items = [
            GroundTruthItem(
                item_id="warfarin-aspirin",
                description="Warfarin + Aspirin: increased bleeding risk",
                match_keywords=[["warfarin"], ["aspirin"]],
                weight="high",
            ),
            GroundTruthItem(
                item_id="metformin-lisinopril",
                description="Metformin + Lisinopril: hypotension risk",
                match_keywords=[["metformin"], ["lisinopril"]],
                weight="moderate",
            ),
            GroundTruthItem(
                item_id="simvastatin-amiodarone",
                description="Simvastatin + Amiodarone: rhabdomyolysis risk",
                match_keywords=[["simvastatin"], ["amiodarone"]],
                weight="high",
            ),
        ]
        return KeywordMatchOracle(items)

    def test_perfect_recall(self) -> None:
        oracle = self._drug_oracle()
        output = (
            "1. Warfarin + Aspirin: increased bleeding risk (high severity)\n"
            "2. Metformin + Lisinopril: hypotension risk (moderate)\n"
            "3. Simvastatin + Amiodarone: rhabdomyolysis risk (high)\n"
        )
        result = oracle.evaluate(output)
        assert result.recall == 1.0
        assert result.found_count == 3

    def test_partial_recall(self) -> None:
        oracle = self._drug_oracle()
        output = "Warfarin and Aspirin have a known bleeding interaction."
        result = oracle.evaluate(output)
        assert result.recall > 0.0
        assert result.recall < 1.0

    def test_zero_recall(self) -> None:
        oracle = self._drug_oracle()
        output = "No significant drug interactions were identified."
        result = oracle.evaluate(output)
        assert result.recall == 0.0
        assert result.found_count == 0

    def test_weight_agreement(self) -> None:
        oracle = self._drug_oracle()
        output = "Warfarin + Aspirin: high severity bleeding interaction."
        result = oracle.evaluate(output)
        assert result.weight_agreement is not None

    def test_default_claim_heuristic_does_not_collapse_precision(self) -> None:
        oracle = self._drug_oracle()
        output = (
            "1. Warfarin + Aspirin: high severity bleeding interaction.\n"
            "2. Vitamin C + Magnesium: benign supplement pairing.\n"
            "3. Fish oil + Ginger: increased bleeding risk.\n"
        )
        result = oracle.evaluate(output)
        assert result.claimed_count == 3
        assert result.false_positive_count == 2
        assert result.precision < 1.0


# ===========================================================================
# KeywordMatchOracle — math proof domain
# ===========================================================================


class TestOracleMathProof:
    def _proof_oracle(self):  # noqa: ANN202
        from autocontext.execution.objective_verification import (
            GroundTruthItem,
            KeywordMatchOracle,
        )

        items = [
            GroundTruthItem(
                item_id="step-1",
                description="Assume P is true (hypothesis)",
                match_keywords=[["assume", "hypothesis", "suppose"], ["p"]],
                weight="moderate",
                category="proof_step",
            ),
            GroundTruthItem(
                item_id="step-2",
                description="Apply modus ponens to derive Q",
                match_keywords=[["modus ponens"]],
                weight="high",
                category="proof_step",
            ),
            GroundTruthItem(
                item_id="step-3",
                description="Conclude Q is true (QED)",
                match_keywords=[["conclude", "therefore", "qed", "thus"], ["q"]],
                weight="high",
                category="proof_step",
            ),
        ]
        return KeywordMatchOracle(items)

    def test_complete_proof(self) -> None:
        oracle = self._proof_oracle()
        output = (
            "Step 1: Assume P is true (hypothesis).\n"
            "Step 2: By modus ponens, since P implies Q and P is true, Q follows.\n"
            "Step 3: Therefore, we conclude Q is true. QED.\n"
        )
        result = oracle.evaluate(output)
        assert result.recall == 1.0

    def test_missing_step(self) -> None:
        oracle = self._proof_oracle()
        output = (
            "Assume P is true.\n"
            "Therefore Q is true. QED.\n"
        )
        result = oracle.evaluate(output)
        # Should find steps 1 and 3 but not step 2 (modus ponens)
        assert result.found_count == 2
        assert result.recall < 1.0


# ===========================================================================
# KeywordMatchOracle — factual claim domain
# ===========================================================================


class TestOracleFactualClaims:
    def test_factual_claims(self) -> None:
        from autocontext.execution.objective_verification import (
            GroundTruthItem,
            KeywordMatchOracle,
        )

        items = [
            GroundTruthItem(
                item_id="capital-france",
                description="The capital of France is Paris",
                match_keywords=[["paris"], ["capital", "france"]],
                weight="low",
            ),
            GroundTruthItem(
                item_id="speed-light",
                description="Speed of light is approximately 300,000 km/s",
                match_keywords=[["speed", "light"], ["300"]],
                weight="moderate",
            ),
        ]
        oracle = KeywordMatchOracle(items)
        output = (
            "The capital of France is Paris, a city on the Seine.\n"
            "The speed of light is approximately 300,000 km/s.\n"
        )
        result = oracle.evaluate(output)
        assert result.recall == 1.0
        assert result.found_count == 2


# ===========================================================================
# KeywordMatchOracle — with claim patterns for false-positive detection
# ===========================================================================


class TestOracleClaimPatterns:
    def test_false_positive_detection(self) -> None:
        from autocontext.execution.objective_verification import (
            GroundTruthItem,
            KeywordMatchOracle,
        )

        items = [
            GroundTruthItem(
                item_id="fact-1",
                description="Water boils at 100C",
                match_keywords=[["boil", "boils"], ["100"]],
                weight="low",
            ),
        ]
        # Claim pattern counts numbered list items as claims
        claim_re = re.compile(r"^\d+\.", re.MULTILINE)
        oracle = KeywordMatchOracle(items, claim_patterns=[claim_re])

        output = (
            "1. Water boils at 100C at sea level.\n"
            "2. Ice melts at 0C.\n"
            "3. The sky is blue due to Rayleigh scattering.\n"
        )
        result = oracle.evaluate(output)
        assert result.found_count == 1
        assert result.claimed_count == 3
        assert result.false_positive_count == 2


# ===========================================================================
# OracleResult
# ===========================================================================


class TestOracleResult:
    def test_construction(self) -> None:
        from autocontext.execution.objective_verification import OracleResult

        result = OracleResult(
            total_known=3, found_count=2, claimed_count=3,
            false_positive_count=1, recall=0.667, precision=0.667,
            weight_agreement=1.0, item_details=[],
        )
        assert abs(result.recall - 0.667) < 0.01

    def test_roundtrip(self) -> None:
        from autocontext.execution.objective_verification import OracleResult

        result = OracleResult(
            total_known=5, found_count=4, claimed_count=5,
            false_positive_count=1, recall=0.8, precision=0.8,
            weight_agreement=0.75, item_details=[],
        )
        d = result.to_dict()
        restored = OracleResult.from_dict(d)
        assert restored.recall == 0.8


# ===========================================================================
# OracleComparison + compare_oracle_vs_rubric
# ===========================================================================


class TestOracleComparison:
    def test_construction(self) -> None:
        from autocontext.execution.objective_verification import OracleComparison

        comp = OracleComparison(
            rubric_score=0.85, objective_recall=0.67,
            objective_precision=0.80, weight_agreement=0.75,
            false_positive_rate=0.20, rubric_objective_gap=0.18,
        )
        assert comp.rubric_objective_gap == 0.18

    def test_summary(self) -> None:
        from autocontext.execution.objective_verification import OracleComparison

        comp = OracleComparison(
            rubric_score=0.90, objective_recall=0.60,
            objective_precision=0.75, weight_agreement=0.50,
            false_positive_rate=0.25, rubric_objective_gap=0.30,
        )
        summary = comp.summary()
        assert "0.90" in summary
        assert "recall" in summary.lower()


class TestObjectiveVerificationConfig:
    def test_roundtrip_and_execution(self) -> None:
        from autocontext.execution.objective_verification import (
            GroundTruthItem,
            ObjectiveVerificationConfig,
            run_objective_verification,
        )

        config = ObjectiveVerificationConfig(
            ground_truth=[
                GroundTruthItem(
                    item_id="warfarin-aspirin",
                    description="Warfarin + Aspirin",
                    match_keywords=[["warfarin"], ["aspirin"]],
                    weight="high",
                )
            ],
            claim_patterns=[r"^\d+\."],
            metadata={"domain": "l19"},
        )

        restored = ObjectiveVerificationConfig.from_dict(config.to_dict())
        payload = run_objective_verification(
            output="1. Warfarin + Aspirin: high severity bleeding interaction.",
            rubric_score=0.8,
            config=restored,
        )

        assert payload["oracle_result"]["found_count"] == 1
        assert payload["comparison"]["objective_recall"] == 1.0
        assert payload["config_metadata"]["domain"] == "l19"


class TestCompareOracleVsRubric:
    def test_comparison(self) -> None:
        from autocontext.execution.objective_verification import (
            OracleResult,
            compare_oracle_vs_rubric,
        )

        oracle_result = OracleResult(
            total_known=3, found_count=2, claimed_count=3,
            false_positive_count=1, recall=0.67, precision=0.67,
            weight_agreement=0.5, item_details=[],
        )
        comparison = compare_oracle_vs_rubric(rubric_score=0.85, oracle_result=oracle_result)
        assert comparison.rubric_score == 0.85
        assert comparison.objective_recall == 0.67
        assert comparison.rubric_objective_gap > 0

    def test_aligned_scores(self) -> None:
        from autocontext.execution.objective_verification import (
            OracleResult,
            compare_oracle_vs_rubric,
        )

        oracle_result = OracleResult(
            total_known=3, found_count=3, claimed_count=3,
            false_positive_count=0, recall=1.0, precision=1.0,
            weight_agreement=1.0, item_details=[],
        )
        comparison = compare_oracle_vs_rubric(rubric_score=0.95, oracle_result=oracle_result)
        assert comparison.rubric_objective_gap < 0.1

    def test_stronger_objective_score_does_not_inflate_gap(self) -> None:
        from autocontext.execution.objective_verification import (
            OracleResult,
            compare_oracle_vs_rubric,
        )

        oracle_result = OracleResult(
            total_known=2, found_count=2, claimed_count=2,
            false_positive_count=0, recall=1.0, precision=1.0,
            weight_agreement=1.0, item_details=[],
        )
        comparison = compare_oracle_vs_rubric(rubric_score=0.6, oracle_result=oracle_result)
        assert comparison.rubric_objective_gap == 0.0
