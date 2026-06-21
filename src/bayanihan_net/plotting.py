"""Figures for the evidence pack.

Pure rendering: every function takes already-computed report data and writes a PNG. We use
the non-interactive Agg backend so plots render headless (CI, no display) and deterministically.
matplotlib is a core dependency; if a figure can't be drawn the caller can skip it without
affecting the numeric evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless, deterministic rendering
import matplotlib.pyplot as plt  # noqa: E402


def save_response_curve(report: Any, path: Path) -> None:
    """River level vs. the response over time: queue depth, resolved, assets committed."""
    rows = report.golden_rows
    ticks = [r["tick"] for r in rows]
    fig, ax1 = plt.subplots(figsize=(9, 4.5))
    ax1.plot(ticks, [r["river_m"] for r in rows], color="tab:blue", lw=2, label="river (m)")
    ax1.axhline(15, color="gold", ls=":", lw=1, label="1st alarm")
    ax1.axhline(18, color="tab:red", ls=":", lw=1, label="3rd alarm")
    ax1.set_xlabel("tick (15 min each)")
    ax1.set_ylabel("river level (m)", color="tab:blue")
    ax2 = ax1.twinx()
    ax2.plot(ticks, [r["open_incidents"] for r in rows], color="tab:orange", label="open incidents")
    ax2.plot(ticks, [r["resolved"] for r in rows], color="tab:green", label="resolved (cum)")
    ax2.plot(
        ticks,
        [r["committed_assets"] for r in rows],
        color="tab:purple",
        ls="--",
        label="assets committed",
    )
    ax2.set_ylabel("incidents / assets")
    fig.suptitle(f"Response timeline -- {report.run_id}")
    _merge_legends(ax1, ax2)
    _save(fig, path)


def save_coverage_bars(report: Any, path: Path) -> None:
    """Per-barangay served fraction (the equity view): which communities got covered."""
    cov = report.outcome["served_fraction_by_barangay"]
    names = list(cov)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(names, [cov[n] for n in names], color="tab:cyan")
    ax.axhline(
        report.outcome["min_served_fraction"],
        color="tab:red",
        ls="--",
        label=f"worst-served = {report.outcome['min_served_fraction']:.2f}",
    )
    ax.set_ylabel("served fraction")
    ax.set_ylim(0, 1)
    ax.set_title(f"Coverage by barangay -- {report.run_id}")
    ax.bar_label(bars, fmt="%.2f", fontsize=8)
    ax.legend()
    plt.xticks(rotation=20, ha="right")
    _save(fig, path)


def save_policy_comparison(summary: dict[str, Any], path: Path) -> None:
    """Grouped bars: each policy on the headline service and equity metrics."""
    per = summary["per_policy"]
    policies = list(per)
    metrics = [
        ("severity_weighted_served_fraction", "sev-wt served"),
        ("min_served_fraction", "worst-served"),
        ("coverage_gini", "coverage Gini (low=fair)"),
    ]
    fig, axes = plt.subplots(1, len(metrics), figsize=(13, 4.2))
    for ax, (key, title) in zip(axes, metrics, strict=True):
        means = [per[p][key]["mean"] for p in policies]
        errs = [per[p][key]["se"] for p in policies]
        colors = ["tab:green" if p == "hybrid" else "tab:gray" for p in policies]
        ax.bar(policies, means, yerr=errs, color=colors, capsize=3)
        ax.set_title(title, fontsize=10)
        ax.tick_params(axis="x", rotation=40, labelsize=8)
    fig.suptitle(f"Policy comparison over {summary['seeds']} paired seeds (hybrid in green)")
    _save(fig, path)


def save_stress_curves(named_reports: dict[str, Any], path: Path) -> None:
    """Unmet need over time for each stress scenario -- does it re-stabilize after the shock?"""
    fig, ax = plt.subplots(figsize=(9, 4.8))
    for name, report in named_reports.items():
        rows = report.golden_rows
        ax.plot([r["tick"] for r in rows], [r["unmet_people"] for r in rows], label=name, lw=1.7)
    ax.set_xlabel("tick (15 min each)")
    ax.set_ylabel("unmet people (open + in-progress)")
    ax.set_title("Stress response: outstanding need over time")
    ax.legend(fontsize=8)
    _save(fig, path)


def save_training_curve(history: list[dict[str, float]], path: Path) -> None:
    """Offline RL routing study: mean episode return vs. training iteration."""
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for key, color in (("risk_aware", "tab:green"), ("naive", "tab:red")):
        series = [(h["iteration"], h[key]) for h in history if key in h]
        if series:
            xs, ys = zip(*series, strict=True)
            ax.plot(xs, ys, label=f"{key} reward", color=color, lw=2)
    ax.set_xlabel("training iteration")
    ax.set_ylabel("mean true return (arrival - travel time - flood exposure)")
    ax.set_title("MARL routing: risk-aware vs. naive reward")
    ax.legend()
    _save(fig, path)


def _merge_legends(ax1: Any, ax2: Any) -> None:
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper right")


def _save(fig: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.tight_layout()
        fig.savefig(path, dpi=120)
    finally:
        plt.close(fig)  # always release the figure, even if rendering raises
