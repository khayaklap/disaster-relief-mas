"""Command-line entry point.

Subcommands (each writes provenance-stamped artifacts under ``evidence/``):

* ``run``   -- one worked scenario under the hybrid policy -> transcript + audit + report.
* ``eval``  -- the hybrid policy vs. baselines across seeds -> results.csv + report.
* ``stress``-- the red-team stress battery -> per-scenario re-stabilization report.
* ``rl-train`` -- the offline MARL routing study -> training curve + comparison.

All four subcommands are live. Everything is reproducible from ``--seed``; no API key or network
is required.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from . import plotting
from .baselines import (
    DEFAULT_SEEDS,
    load_scenarios,
    run_matrix,
    run_stress,
    stress_invariants,
    summarize,
)
from .config import SEED, config_with
from .engine import Engine, RunReport
from .messages import MsgType
from .provenance import tooling_provenance

_DEFAULT_SCENARIOS = Path(__file__).resolve().parents[2] / "evals" / "scenarios.jsonl"


# ---------------------------------------------------------------------------
# evidence writers
# ---------------------------------------------------------------------------
def write_evidence(engine: Engine, report: RunReport, outdir: Path) -> None:
    """Write the four standard evidence artifacts for a single run."""
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "scenario_report.json").write_text(
        json.dumps(report.to_dict(), indent=2), encoding="utf-8"
    )
    with (outdir / "audit_log.jsonl").open("w", encoding="utf-8") as fh:
        for event in report.events:
            fh.write(json.dumps(event) + "\n")
    (outdir / "decision_package.json").write_text(
        json.dumps(_first_decision_package(engine), indent=2), encoding="utf-8"
    )
    (outdir / "run_log.txt").write_text(_run_log(engine, report), encoding="utf-8")


def _first_decision_package(engine: Engine) -> dict[str, Any]:
    """The first HITL decision package raised in the run (the worked governance example)."""
    request = next((e for e in engine.bus.log if e.msg_type is MsgType.APPROVAL_REQUESTED), None)
    if request is None:
        return {"note": "no human-in-the-loop approval was triggered in this run"}
    req_payload = request.typed_payload()
    decision = next(
        (
            e.typed_payload()
            for e in engine.bus.log
            if e.msg_type is MsgType.APPROVAL_DECISION
            and e.correlation_id == req_payload.request_id  # type: ignore[attr-defined]
        ),
        None,
    )
    return {
        "request": req_payload.model_dump(),
        "decision": decision.model_dump() if decision is not None else None,
    }


def _run_log(engine: Engine, report: RunReport) -> str:
    """A readable transcript: provenance, one traced incident lifecycle, and the summary."""
    p = report.provenance
    o, e = report.outcome, report.emergence
    lines: list[str] = []
    add = lines.append
    add("=" * 78)
    add("BAYANIHAN-NET  --  worked scenario transcript")
    add("=" * 78)
    add(f"run_id           : {report.run_id}")
    add(f"scenario / seed  : {p['scenario']} / {p['seed']}")
    add(
        f"policy           : {p['policy']}  (fairness={p['use_fairness']}, "
        f"governance={p['use_governance']})"
    )
    add(f"python / fp      : {p['python']} / {p['env_fingerprint']}")
    add("libraries        : " + ", ".join(f"{k}={v}" for k, v in p["libraries"].items()))
    add("")
    add("-- one incident, end to end " + "-" * 50)
    for line in _trace_one_incident(report.events):
        add("  " + line)
    add("")
    add("-- system outcome " + "-" * 60)
    add(f"  genuine incidents      : {o['genuine_incidents']}")
    add(
        f"  people: need / served  : {o['total_need_people']} / {o['total_served_people']} "
        f"({o['served_fraction']:.1%})"
    )
    add(f"  SLA compliance         : {o['sla_compliance']:.1%}  (served late: {o['served_late']})")
    add(f"  unmet-need Gini        : {o['gini_unmet']:.3f}")
    add(f"  unmet by barangay      : {o['unmet_by_barangay']}")
    if o["sybil_reports_seen"]:
        add(
            f"  sybil seen/suppressed/served : {o['sybil_reports_seen']}/"
            f"{o['sybil_suppressed']}/{o['sybil_served']}"
        )
    add("")
    add("-- coordination & safety " + "-" * 53)
    add(f"  commitments            : {e['commitments']}")
    add(f"  HITL approvals / denied: {e['hitl_approvals']} / {e['hitl_denied']}")
    add(f"  escalations            : {e['escalations']}")
    add(f"  rollbacks (reassign)   : {e['rollbacks']}  (rate {e['reassignment_rate']:.3f})")
    add(f"  messages dropped       : {e['messages_dropped']}")
    add(f"  load-spread entropy    : {e['load_spread_entropy']:.3f}")
    add("=" * 78)
    return "\n".join(lines) + "\n"


def _trace_one_incident(events: list[dict[str, Any]]) -> list[str]:
    """Pull the lifecycle of the first awarded incident from the audit log."""
    award = next((ev for ev in events if ev["event"] == "awarded"), None)
    if award is None:
        return ["(no award occurred in this run)"]
    inc_id = award["incident_id"]
    asset_id = award["asset_id"]
    out: list[str] = []
    for ev in events:
        if ev.get("incident_id") == inc_id or ev.get("asset_id") == asset_id:
            detail = {k: v for k, v in ev.items() if k not in ("tick", "event")}
            out.append(f"t={ev['tick']:>2}  {ev['event']:<22} {detail}")
            if ev["event"] == "asset_freed":
                break
    return out


def print_summary(report: RunReport) -> None:
    """Print the one-line operator dashboard for a run: served fraction, SLA, Gini, and HITL load."""
    o, e = report.outcome, report.emergence
    print(
        f"[{report.run_id}] served {o['total_served_people']}/{o['total_need_people']} "
        f"({o['served_fraction']:.1%}), SLA {o['sla_compliance']:.1%}, "
        f"Gini {o['gini_unmet']:.3f}, escalations {e['escalations']}, "
        f"HITL {e['hitl_approvals']}+/{e['hitl_denied']}-"
    )


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    """`run`: simulate one worked scenario and write its stamped evidence artifacts."""
    cfg = config_with(args.seed)
    engine = Engine(
        cfg,
        policy_name="fairness_weighted" if args.fairness else "hybrid",
        use_fairness=args.fairness,
        order_mode="fairness" if args.fairness else "severity",
        use_governance=not args.no_governance,
    )
    report = engine.run()
    out = Path(args.out)
    write_evidence(engine, report, out)
    figs = Path(args.figures)
    plotting.save_response_curve(report, figs / "response_timeline.png")
    plotting.save_coverage_bars(report, figs / "coverage_by_barangay.png")
    print_summary(report)
    print(f"  evidence -> {out.resolve()}   figures -> {figs.resolve()}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Hybrid vs. baselines across paired seeds -> results.csv + eval_report.json + figure."""
    seeds = tuple(args.seeds) if args.seeds else DEFAULT_SEEDS
    reports = run_matrix(seeds=seeds)
    summary = summarize(reports)
    summary["provenance"] = tooling_provenance(seeds=list(seeds))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    _write_csv([r.summary_row() for r in reports], out / "results.csv")
    (out / "eval_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    plotting.save_policy_comparison(summary, Path(args.figures) / "policy_comparison.png")
    print(f"[eval] {summary['seeds']} seeds x {len(summary['per_policy'])} policies")
    for policy, metrics in summary["per_policy"].items():
        print(
            f"  {policy:15s} sevW={metrics['severity_weighted_served_fraction']['mean']:.3f} "
            f"worst-served={metrics['min_served_fraction']['mean']:.3f} "
            f"covGini={metrics['coverage_gini']['mean']:.3f}"
        )
    print(f"  evidence -> {out.resolve()}")
    return 0


def cmd_stress(args: argparse.Namespace) -> int:
    """Red-team stress battery: run each scenario, check re-stabilization, write report."""
    scenarios = load_scenarios(args.scenarios)
    results: dict[str, Any] = {}
    named_reports = {}
    all_passed = True
    for sc in scenarios:
        report = run_stress(sc, seed=args.seed)
        checks = stress_invariants(report)
        all_passed = all_passed and checks["passed"]
        named_reports[sc.name] = report
        results[sc.name] = {
            "description": sc.description,
            "overrides": sc.overrides,
            "checks": checks,
            "outcome": {
                k: report.outcome[k]
                for k in (
                    "served_fraction",
                    "severity_weighted_served_fraction",
                    "min_served_fraction",
                    "sla_compliance",
                    "sybil_served",
                    "sybil_suppressed",
                )
            },
            "emergence": {
                k: report.emergence[k]
                for k in (
                    "escalations",
                    "rollbacks",
                    "messages_dropped",
                    "reassignment_rate",
                )
            },
        }
        flag = "PASS" if checks["passed"] else "FAIL"
        print(
            f"  [{flag}] {sc.name:16s} served={report.outcome['served_fraction']:.2f} "
            f"worst={report.outcome['min_served_fraction']:.2f} "
            f"escal={report.emergence['escalations']} drops={report.emergence['messages_dropped']}"
        )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stress_report.json").write_text(
        json.dumps(
            {
                "provenance": tooling_provenance(seed=args.seed),
                "all_passed": all_passed,
                "scenarios": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    plotting.save_stress_curves(named_reports, Path(args.figures) / "stress_response.png")
    print(f"  {'ALL PASSED' if all_passed else 'SOME FAILED'}  evidence -> {out.resolve()}")
    return 0 if all_passed else 1


def cmd_rl(args: argparse.Namespace) -> int:
    """Offline MARL routing study: naive vs. risk-aware reward -> rl_training.json + curve."""
    from .rl.evaluate import run_study

    study = run_study(config_with(args.seed).scenario, episodes=args.episodes, seed=args.seed)
    study["provenance"] = tooling_provenance(seed=args.seed, episodes=args.episodes)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rl_training.json").write_text(json.dumps(study, indent=2), encoding="utf-8")
    plotting.save_training_curve(study["history"], Path(args.figures) / "rl_training_curve.png")
    f, v = study["final"], study["verdict"]
    print(
        f"[rl-train] {study['episodes']} episodes, seed {study['seed']}, "
        f"{study['test_episodes']} test routes"
    )
    for name in ("naive", "risk_aware", "heuristic"):
        print(
            f"  {name:11s} true_return={f[name]['true_return']:.3f} "
            f"flood_exposure={f[name]['mean_cumulative_risk']:.3f} "
            f"arrival={f[name]['arrival_rate']:.2f}"
        )
    print(
        f"  reward_hacking_demonstrated = {v['reward_hacking_demonstrated']}; "
        f"learned_beats_heuristic = {v['learned_beats_heuristic']}"
    )
    print(f"  evidence -> {out.resolve()}")
    return 0


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI with the run / eval / stress / rl-train subcommands."""
    parser = argparse.ArgumentParser(prog="bayanihan-net", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one worked scenario and write evidence")
    run.add_argument("--seed", type=int, default=SEED)
    run.add_argument("--out", default="evidence", help="evidence output directory")
    run.add_argument("--figures", default="figures", help="figure output directory")
    run.add_argument("--fairness", action="store_true", help="use equity-weighted ordering")
    run.add_argument("--no-governance", action="store_true", help="disable the HITL gate")
    run.set_defaults(func=cmd_run)

    ev = sub.add_parser("eval", help="hybrid vs. baselines across seeds -> results.csv")
    ev.add_argument("--seeds", type=int, nargs="*", default=None, help="override seed list")
    ev.add_argument("--out", default="evidence")
    ev.add_argument("--figures", default="figures")
    ev.set_defaults(func=cmd_eval)

    st = sub.add_parser("stress", help="red-team stress battery -> stress_report.json")
    st.add_argument("--seed", type=int, default=SEED)
    st.add_argument("--scenarios", default=str(_DEFAULT_SCENARIOS))
    st.add_argument("--out", default="evidence")
    st.add_argument("--figures", default="figures")
    st.set_defaults(func=cmd_stress)

    rl = sub.add_parser("rl-train", help="offline MARL routing study (numpy; no torch needed)")
    rl.add_argument("--seed", type=int, default=SEED)
    rl.add_argument("--episodes", type=int, default=6000)
    rl.add_argument("--out", default="evidence")
    rl.add_argument("--figures", default="figures")
    rl.set_defaults(func=cmd_rl)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse arguments and dispatch to the selected subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
