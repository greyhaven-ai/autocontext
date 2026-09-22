"""Freeze once, then run paired AC-1029 trials through the serving route."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sys
import threading
import time
from pathlib import Path

from autocontext.artifacts.policy_candidate import json_payload
from autocontext.context_bundles.models import ContextBundle, stable_digest
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution.executable_skills import (
    SCENARIO,
    SkillSourceEvidence,
    _json,
    evaluator_identity,
    inspect_executable_skill,
    propose_schema_migration,
)
from autocontext.execution.routed_model import model_system_prompt
from autocontext.execution.skill_routing import route_schema_migration, routing_evaluator_identity
from autocontext.execution.skill_routing_models import SkillRoutingConfig
from autocontext.harness import benchmark_stats
from autocontext.knowledge.harness_entries import SkillReference
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE
from autocontext.training.model_registry import ModelRegistry
from autocontext.util.json_io import write_json
from benchmarks.skill_reuse.contracts import ARMS, Corpus, DiscoveryCost, LearningRecord, Models, StudyProtocol
from benchmarks.skill_reuse.reporting import build_report, render_report, score_case

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parents[1]
SNAPSHOTS = ("protocol.json", "corpus.json", "models.json", "learning.json", "skill.py", "playbook.md")


def read_bounded(path: Path) -> str:
    with path.open("rb") as stream:
        value = stream.read(2 * 1024 * 1024 + 1)
    if len(value) > 2 * 1024 * 1024:
        raise ValueError("study input exceeds 2 MiB")
    return value.decode("utf-8")


def fingerprint() -> dict:
    sources = [*sorted(HERE.glob("*.py")), Path(benchmark_stats.__file__), PACKAGE / "uv.lock"]
    return {"router": routing_evaluator_identity(), "python": platform.python_version(),
            "system": platform.system(), "machine": platform.machine(), "runtime_image": PINNED_PYTHON_RUNTIME_IMAGE,
            "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}


def learning_packet(corpus: Corpus) -> dict:
    # Only the permissible split is exported. No development/held-out cases or
    # verifier source is supplied to either representation's author/synthesizer.
    return {"training_digest": corpus.training_digest,
            "training": [case.model_dump(mode="json") for case in corpus.training]}


def validate_inputs(protocol, corpus, models, learning, playbook):
    if learning.training_digest != corpus.training_digest:
        raise ValueError("learning evidence must match the training split")
    if models.evidence_kind == "live" and learning.origin == "fixture":
        raise ValueError("fixture artifacts cannot become live efficacy evidence")
    if not playbook.strip():
        raise ValueError("the learned textual control requires a nonempty playbook")
    SkillRoutingConfig(learned_playbook=playbook)
    for cohort in ("supported", "shifted"):
        cases = [c for c in corpus.heldout if c.cohort == cohort]
        if len(cases) < protocol.min_cases_per_cohort or len({c.group for c in cases}) < protocol.min_groups_per_cohort:
            raise ValueError("held-out sample does not meet the predeclared plan")
    for target in (models.reference, models.cheaper):
        if (target.max_input_tokens + target.max_output_tokens > protocol.request_budget.max_tokens
                or target.cost(target.max_input_tokens, target.max_output_tokens) > protocol.request_budget.max_model_cost_usd):
            raise ValueError("model reservation exceeds a matched request budget")
        if any(len(case.input_json.encode()) + len(model_system_prompt(playbook).encode()) + 2048 > target.max_input_tokens
               for case in (*corpus.training, *corpus.development, *corpus.heldout)):
            raise ValueError("model input bound cannot cover all cases plus the learned playbook")


def freeze(protocol_path, corpus_path, models_path, learning_path, skill_path, playbook_path, output):
    protocol = StudyProtocol.model_validate(_json(read_bounded(protocol_path)))
    corpus = Corpus.model_validate(_json(read_bounded(corpus_path)))
    models = Models.model_validate(_json(read_bounded(models_path)))
    learning = LearningRecord.model_validate(_json(read_bounded(learning_path)))
    source, playbook = read_bounded(skill_path), read_bounded(playbook_path)
    validate_inputs(protocol, corpus, models, learning, playbook)
    output.mkdir(parents=True, exist_ok=False)
    for name, value in (("protocol.json", protocol), ("corpus.json", corpus),
                        ("models.json", models), ("learning.json", learning)):
        write_json(output / name, value.model_dump(mode="json"))
    (output / "skill.py").write_text(source, encoding="utf-8")
    (output / "playbook.md").write_text(playbook, encoding="utf-8")
    store = ContextBundleStore(output / "knowledge")
    store.bootstrap(ContextBundle.create(scenario=SCENARIO, evaluator_epoch=evaluator_identity(), components=[]))
    evidence = tuple(SkillSourceEvidence.create(
        case.id, "example" if case.cohort == "supported" else "counterexample", case.model_dump(mode="json"))
        for case in corpus.training)
    bundle = propose_schema_migration(store, SkillReference(entrypoint="choose_action", source=source),
                                      run_id="ac1029-frozen-study", source_evidence=evidence)
    manifest = {"schema_version": "ac1029.freeze.v1", "environment": fingerprint(), "bundle_digest": bundle.digest,
                "skill_manifest_digest": inspect_executable_skill(store, bundle.digest).digest,
                "training_digest": corpus.training_digest, "active_pointer": store.active_pointer(SCENARIO),
                "files": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in SNAPSHOTS}}
    write_json(output / "freeze.json", {**manifest, "digest": stable_digest(manifest)})
    return {**manifest, "digest": stable_digest(manifest)}


def load_frozen(root: Path):
    manifest = _json(read_bounded(root / "freeze.json"))
    claimed = manifest.pop("digest")
    if claimed != stable_digest(manifest) or set(manifest["files"]) != set(SNAPSHOTS):
        raise ValueError("invalid freeze identity")
    if manifest["environment"] != fingerprint():
        raise ValueError("study code, evaluator or environment changed; fresh freeze/evidence required")
    for name in SNAPSHOTS:
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != manifest["files"][name]:
            raise ValueError("frozen artifact changed")
    protocol = StudyProtocol.model_validate(_json(read_bounded(root / "protocol.json")))
    corpus = Corpus.model_validate(_json(read_bounded(root / "corpus.json")))
    models = Models.model_validate(_json(read_bounded(root / "models.json")))
    learning = LearningRecord.model_validate(_json(read_bounded(root / "learning.json")))
    playbook = read_bounded(root / "playbook.md")
    validate_inputs(protocol, corpus, models, learning, playbook)
    store = ContextBundleStore(root / "knowledge")
    if inspect_executable_skill(store, manifest["bundle_digest"]).digest != manifest["skill_manifest_digest"]:
        raise ValueError("frozen skill changed")
    if store.active_pointer(SCENARIO) != manifest["active_pointer"]:
        raise ValueError("study active pointer changed")
    return manifest, claimed, protocol, corpus, models, learning, playbook, store


def schedule(cases, seed):
    """Seeded paired case order; Latin rotation balances each arm's position."""
    ordered = list(cases)
    rng = random.Random(seed)
    rng.shuffle(ordered)
    arms = list(ARMS)
    rng.shuffle(arms)
    positions = {"supported": 0, "shifted": 0}
    result = []
    for case in ordered:
        position = positions[case.cohort] % 4
        result.append((case, arms[position:] + arms[:position]))
        positions[case.cohort] += 1
    return result


def with_development_costs(root, learning):
    """Carry every post-freeze development arm into the final lifecycle ledger."""
    directory = root / "development"
    if not directory.exists():
        return learning
    report = _json(read_bounded(directory / "summary.json"))
    if report["status"] != "complete":
        raise ValueError("incomplete development requires a new freeze with retained failed-attempt costs")
    rows = _json(read_bounded(directory / "episodes.json"))
    digest = stable_digest(rows)
    costs = tuple(DiscoveryCost(
        id=f"post-freeze-development-{arm}", phase="development", arms=(arm,),
        seconds=sum(r["seconds"] for r in rows if r["arm"] == arm),
        model_calls=sum(r["model_calls"] for r in rows if r["arm"] == arm),
        tokens=sum(r["tokens"] for r in rows if r["arm"] == arm), total_cost_usd=None,
        evidence=f"development/episodes.json sha256:{digest}") for arm in ARMS)
    return LearningRecord.model_validate({**learning.model_dump(mode="json"),
                                         "discovery": [r.model_dump(mode="json") for r in (*learning.discovery, *costs)]})


def run(root: Path, *, split="heldout", cancel=None):
    if split not in {"development", "heldout"}:
        raise ValueError("explicit evaluation split required")
    root = root.resolve()
    manifest, digest, protocol, corpus, models, learning, playbook, store = load_frozen(root)
    if split == "heldout":
        learning = with_development_costs(root, learning)
    # An exclusive directory prevents silent reruns, overwrite, and partial-run resume.
    output = root / split
    output.mkdir(exist_ok=False)
    registry = ModelRegistry(output / "models")
    cancel = cancel if cancel is not None else threading.Event()
    deadline = time.monotonic() + protocol.evaluation_wall_seconds
    cases = getattr(corpus, split)
    order = schedule(cases, protocol.bootstrap_seed)
    write_json(output / "schedule.json", [{"case_id": c.id, "arms": arms} for c, arms in order])
    rows = []
    ledger = []
    status = "complete"
    failure = None
    targets = {arm: models.cheaper if arm == "cheap_textual" else models.reference for arm in ARMS}
    pair_calls = 4
    pair_tokens = sum(t.max_input_tokens + t.max_output_tokens for t in targets.values())
    pair_cost = sum(t.cost(t.max_input_tokens, t.max_output_tokens) for t in targets.values())
    try:
        for case, arms in order:
            # Verify exact code/artifacts again before each pair. No later tuning.
            load_frozen(root)
            if cancel.is_set():
                status = "cancelled"
                break
            if (sum(r["model_calls"] for r in rows) + pair_calls > protocol.max_evaluation_calls
                    or sum(r["tokens"] for r in rows) + pair_tokens > protocol.max_evaluation_tokens
                    or sum(r["declared_model_cost_usd"] for r in rows) + pair_cost > protocol.max_evaluation_model_cost_usd
                    or time.monotonic() + 4 * protocol.request_budget.wall_seconds > deadline):
                status = "evaluation_budget_exhausted"
                break
            for arm in arms:
                if cancel.is_set() or time.monotonic() >= deadline:
                    status = "cancelled" if cancel.is_set() else "evaluation_budget_exhausted"
                    break
                target = targets[arm]
                budget = protocol.request_budget.model_copy(update={
                    "wall_seconds": min(protocol.request_budget.wall_seconds, deadline - time.monotonic())})
                config = SkillRoutingConfig(
                    enabled=True, allow_network=True, general=target, budget=budget,
                    bundle_digest=manifest["bundle_digest"] if arm == "executable" else None,
                    learned_playbook="" if arm == "baseline" else playbook)
                ledger.append({"case_id": case.id, "arm": arm, "state": "reserved",
                               "reserved_model_calls": 1, "reserved_tokens": target.max_input_tokens + target.max_output_tokens,
                               "reserved_model_cost_usd": target.cost(target.max_input_tokens, target.max_output_tokens),
                               "config_digest": config.digest})
                write_json(output / "ledger.json", ledger)
                started = time.monotonic()
                result = route_schema_migration(store, registry, case.input_json, config=config,
                                                 trace_root=output / "traces", mode="evaluation", cancel=cancel)
                row = score_case(case, result)
                row.update(arm=arm, seconds=time.monotonic() - started,
                           cpu_seconds=None, peak_memory_bytes=None, total_cost_usd=None)
                rows.append(row)
                ledger[-1].update(state="completed", model_calls=result.model_calls,
                                  tokens=result.accounted_tokens, declared_model_cost_usd=result.accounted_model_cost_usd,
                                  trace_path=result.trace_path)
                write_json(output / "ledger.json", ledger)
                write_json(output / "episodes.json", rows)
                if result.reason in {"cleanup_unverified", "model_cleanup_unverified", "invalid_provider_receipt",
                                      "provider_accounting_unverified",
                                      "reported_model_mismatch", "cancelled"}:
                    status = "unverified_or_cancelled_execution"
                    break
            if status != "complete":
                break
    except BaseException as exc:
        status, failure = "interrupted", type(exc).__name__
        raise
    finally:
        try:
            load_frozen(root)
        except (ValueError, OSError, KeyError):
            status = "frozen_identity_changed"
        if store.active_pointer(SCENARIO) != manifest["active_pointer"]:
            status = "active_pointer_changed"
        report = build_report(rows, protocol, learning, evidence_kind=models.evidence_kind, split=split,
                              expected_cases=len(cases), status=status)
        report.update(freeze_digest=digest, failure=failure, discovery_ledger=learning.model_dump(mode="json")["discovery"],
                      unreconciled_reservations=[r for r in ledger if r["state"] == "reserved"])
        write_json(output / "summary.json", report)
        (output / "REPORT.md").write_text(render_report(report), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    packet = commands.add_parser("learning-packet")
    packet.add_argument("--corpus", type=Path, required=True)
    packet.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("freeze")
    for field in ("protocol", "corpus", "models", "learning", "skill", "playbook", "output"):
        prepare.add_argument("--" + field, type=Path, required=True)
    execute = commands.add_parser("run")
    execute.add_argument("--frozen", type=Path, required=True)
    execute.add_argument("--split", choices=("development", "heldout"), required=True)
    args = parser.parse_args()
    if args.command == "learning-packet":
        packet_data = learning_packet(Corpus.model_validate(_json(read_bounded(args.corpus))))
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json_payload(packet_data) + "\n")
        print(packet_data["training_digest"])
    elif args.command == "freeze":
        result = freeze(args.protocol, args.corpus, args.models, args.learning, args.skill, args.playbook, args.output)
        print(result["digest"])
    else:
        result = run(args.frozen, split=args.split)
        print(json.dumps({"status": result["status"], "decision": result["decision"]}))
        if result["status"] != "complete":
            sys.exit(1)


if __name__ == "__main__":
    main()
