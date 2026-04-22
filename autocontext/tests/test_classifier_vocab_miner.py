"""AC-582 — mine cached LLM classifications to propose keyword vocabulary additions."""
from __future__ import annotations

from autocontext.scenarios.custom.classifier_cache import (
    ClassifierCache,
    _schema_version,
)
from autocontext.scenarios.custom.classifier_vocab_miner import (
    VocabProposal,
    format_proposals_report,
    load_cache_entries,
    mine_vocab_proposals,
)
from autocontext.scenarios.custom.family_classifier import FamilyClassification
from autocontext.scenarios.families import list_families

# Minimal signal groups for tests — real classifier has 11 families and many keywords.
_SIGNAL_GROUPS = {
    "simulation": {"pipeline": 1.5, "rollback": 2.0},
    "agent_task": {"essay": 2.0, "haiku": 1.5},
    "game": {"tournament": 2.0},
}


def _entry(description: str, family: str) -> dict:
    """Shape of a ClassifierCache entry."""
    return {"description": description, "family_name": family}


class TestMineVocabProposals:
    def test_empty_cache_returns_no_proposals(self) -> None:
        proposals = mine_vocab_proposals([], _SIGNAL_GROUPS, min_occurrences=3)
        assert proposals == []

    def test_proposes_term_that_recurs_for_a_family(self) -> None:
        cache = [
            _entry("biomedical drug interaction study", "agent_task"),
            _entry("biomedical research protocol design", "agent_task"),
            _entry("biomedical literature summarization", "agent_task"),
        ]
        proposals = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=3)

        terms = {p.term for p in proposals}
        assert "biomedical" in terms
        biomedical = next(p for p in proposals if p.term == "biomedical")
        assert biomedical.family_name == "agent_task"
        assert biomedical.occurrence_count == 3

    def test_does_not_propose_existing_signal(self) -> None:
        # "pipeline" is already in simulation signals; must not be proposed
        # even if it recurs in the cache for that family.
        cache = [
            _entry("deploy a pipeline to staging", "simulation"),
            _entry("failover pipeline with rollback", "simulation"),
            _entry("multi-stage pipeline orchestration", "simulation"),
        ]
        proposals = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=3)
        assert not any(p.term == "pipeline" for p in proposals)

    def test_does_not_propose_substring_of_existing_signal(self) -> None:
        # The simulation group has "orchestrat" as a prefix match for
        # "orchestrate" / "orchestration". A candidate term "orchestrate"
        # must not be proposed because it's already covered.
        groups_with_prefix = {
            "simulation": {"orchestrat": 2.0},
        }
        cache = [
            _entry("orchestrate a deployment", "simulation"),
            _entry("orchestrate retries carefully", "simulation"),
            _entry("orchestrate multi-stage rollouts", "simulation"),
        ]
        proposals = mine_vocab_proposals(cache, groups_with_prefix, min_occurrences=3)
        assert not any(p.term == "orchestrate" for p in proposals)

    def test_skips_stopwords(self) -> None:
        cache = [
            _entry("and the a an of for to in on", "agent_task"),
            _entry("and the a an of for to in on", "agent_task"),
            _entry("and the a an of for to in on", "agent_task"),
        ]
        proposals = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=3)
        assert proposals == []

    def test_respects_min_occurrences_threshold(self) -> None:
        cache = [
            _entry("cryptographic protocol analysis", "agent_task"),
            _entry("cryptographic key derivation", "agent_task"),
        ]
        proposals = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=3)
        assert not any(p.term == "cryptographic" for p in proposals)

        proposals_lower = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=2)
        assert any(p.term == "cryptographic" for p in proposals_lower)

    def test_counts_distinct_descriptions_not_repeated_tokens(self) -> None:
        cache = [
            _entry("biomedical biomedical biomedical protocol", "agent_task"),
        ]
        proposals = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=3)
        assert not any(p.term == "biomedical" for p in proposals)

    def test_proposal_includes_example_descriptions(self) -> None:
        cache = [
            _entry("quantum circuit simulation one", "simulation"),
            _entry("quantum circuit simulation two", "simulation"),
            _entry("quantum annealing walkthrough", "simulation"),
        ]
        proposals = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=3)
        quantum = next(p for p in proposals if p.term == "quantum")
        assert len(quantum.example_descriptions) >= 1
        assert len(quantum.example_descriptions) <= 3
        assert all("quantum" in ex.lower() for ex in quantum.example_descriptions)

    def test_proposals_sorted_by_count_desc_within_family(self) -> None:
        cache = [
            _entry("forensic analysis report", "agent_task"),
            _entry("forensic audit trail", "agent_task"),
            _entry("forensic evidence summary", "agent_task"),
            _entry("forensic report writeup", "agent_task"),
            _entry("subpoena response letter", "agent_task"),
            _entry("subpoena compliance review", "agent_task"),
            _entry("subpoena draft package", "agent_task"),
        ]
        proposals = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=3)
        agent_task_proposals = [p for p in proposals if p.family_name == "agent_task"]
        counts = [p.occurrence_count for p in agent_task_proposals]
        assert counts == sorted(counts, reverse=True)

    def test_returns_vocab_proposal_instances(self) -> None:
        cache = [
            _entry("logistics routing planner", "simulation"),
            _entry("logistics warehouse turnover", "simulation"),
            _entry("logistics freight lane design", "simulation"),
        ]
        proposals = mine_vocab_proposals(cache, _SIGNAL_GROUPS, min_occurrences=3)
        assert all(isinstance(p, VocabProposal) for p in proposals)


class TestFormatProposalsReport:
    def test_empty_proposals_produces_non_empty_report(self) -> None:
        report = format_proposals_report([], total_cache_entries=0)
        # Still renders a helpful report even with no proposals.
        assert report.strip() != ""
        assert "No vocabulary proposals" in report or "0 proposals" in report

    def test_report_includes_family_term_count_and_examples(self) -> None:
        proposals = [
            VocabProposal(
                family_name="agent_task",
                term="biomedical",
                suggested_weight=1.5,
                occurrence_count=4,
                example_descriptions=["biomedical drug study", "biomedical research"],
            ),
        ]
        report = format_proposals_report(proposals, total_cache_entries=4)
        assert "agent_task" in report
        assert "biomedical" in report
        assert "4" in report  # occurrence count somewhere
        assert "biomedical drug study" in report

    def test_report_groups_by_family(self) -> None:
        proposals = [
            VocabProposal(
                family_name="simulation",
                term="logistics",
                suggested_weight=1.5,
                occurrence_count=3,
                example_descriptions=["logistics routing"],
            ),
            VocabProposal(
                family_name="agent_task",
                term="biomedical",
                suggested_weight=1.5,
                occurrence_count=3,
                example_descriptions=["biomedical analysis"],
            ),
        ]
        report = format_proposals_report(proposals, total_cache_entries=6)
        # Each family has its own section heading.
        assert report.count("### Family:") == 2 or report.count("## Family:") == 2


class TestLoadCacheEntriesFromClassifierCache:
    """Miner consumes the AC-581 cache file directly."""

    _FAMILIES = [family.name for family in list_families()]

    def _classification(self, family: str) -> FamilyClassification:
        return FamilyClassification(
            family_name=family,
            confidence=0.8,
            rationale="r",
            no_signals_matched=False,
        )

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        assert load_cache_entries(tmp_path / "missing.json") == []

    def test_corrupt_file_returns_empty(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_cache_entries(path) == []

    def test_stale_schema_returns_empty(self, tmp_path) -> None:
        import json

        path = tmp_path / "cache.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "stale-schema",
                    "entries": {
                        "deadbeef": {
                            "family_name": "deleted_family",
                            "description": "novel domain token",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        assert load_cache_entries(path) == []

    def test_current_schema_is_accepted(self, tmp_path) -> None:
        import json

        path = tmp_path / "cache.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": _schema_version(self._FAMILIES),
                    "entries": {
                        "deadbeef": {
                            "family_name": "agent_task",
                            "description": "biomedical protocol review",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        assert load_cache_entries(path) == [
            {"description": "biomedical protocol review", "family_name": "agent_task"}
        ]

    def test_returns_description_and_family_from_cache_entries(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        cache = ClassifierCache(path)
        cache.put("biomedical study one", self._FAMILIES, self._classification("agent_task"))
        cache.put("biomedical study two", self._FAMILIES, self._classification("agent_task"))
        cache.put("failover runbook", self._FAMILIES, self._classification("simulation"))

        entries = load_cache_entries(path)
        assert len(entries) == 3
        descriptions = {e["description"] for e in entries}
        assert descriptions == {
            "biomedical study one",
            "biomedical study two",
            "failover runbook",
        }
        assert {e["family_name"] for e in entries} == {"agent_task", "simulation"}

    def test_end_to_end_cache_to_proposals(self, tmp_path) -> None:
        # Seed the cache with recurring "biomedical" in agent_task descriptions,
        # then mine — proposal should surface.
        path = tmp_path / "cache.json"
        cache = ClassifierCache(path)
        for desc in [
            "biomedical drug study design",
            "biomedical literature summary",
            "biomedical research protocol",
        ]:
            cache.put(desc, self._FAMILIES, self._classification("agent_task"))

        entries = load_cache_entries(path)
        proposals = mine_vocab_proposals(entries, _SIGNAL_GROUPS, min_occurrences=3)
        assert any(p.term == "biomedical" and p.family_name == "agent_task" for p in proposals)


class TestClassifierCacheStoresDescription:
    """The cache now retains the raw description in each entry so mining can work."""

    def test_put_writes_description_in_entry_value(self, tmp_path) -> None:
        import json

        path = tmp_path / "cache.json"
        cache = ClassifierCache(path)
        cache.put(
            "some niche biomedical prompt",
            ["agent_task", "simulation"],
            FamilyClassification(
                family_name="agent_task",
                confidence=0.8,
                rationale="r",
                no_signals_matched=False,
            ),
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        entry = next(iter(data["entries"].values()))
        assert entry["description"] == "some niche biomedical prompt"

    def test_get_still_works_with_new_field(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        cache = ClassifierCache(path)
        cache.put(
            "a description",
            ["agent_task"],
            FamilyClassification(
                family_name="agent_task",
                confidence=0.8,
                rationale="r",
                no_signals_matched=False,
            ),
        )
        fetched = cache.get("a description", ["agent_task"])
        assert fetched is not None
        assert fetched.family_name == "agent_task"
