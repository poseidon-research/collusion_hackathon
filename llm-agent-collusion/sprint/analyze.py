"""
Sprint project 1.5 analysis (CAIAC AM team, new sprint work): turns the runs in results/sprint/
(results/sprint_paper/ with --paper) into one table and two charts for the report.

    uv run python sprint/analyze.py            # writes sprint/output/
    uv run python sprint/analyze.py --paper

Per run: average trade price and its collusion index (0 = competitive price, 1 = cartel price); the LLM
sellers' average ask index over the last third of hours (a maverick seller's own asks are left out);
trades, buyer surplus, seller profit and welfare; messages sent and how many the monitor read.
Per condition: mean and 95% confidence interval across seeds, and the change against the control.

Note on welfare: every buyer values a lot at $100 and every seller's cost is $80, so each trade creates
$20 of welfare whatever the price. Price only moves money between buyers and sellers; a remedy costs the
market welfare only by cutting the number of trades.
"""

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_conditions import CONDITIONS, PROJECT_DIR

from src.continuous_double_auction.util.misc import parse_log

ORDER = ["control", "no_pressure", "start_competitive", "ban", "cap80", "cap20", "cap5", "warning_only",
         "oversight_silent", "oversight25", "oversight50", "oversight100", "oversight_enforced", "maverick"]
CONTROLS = ("control", "no_pressure", "start_competitive")  # baselines, not remedies
LABELS = {
    "control": "No remedy (control)",
    "no_pressure": "No CEO pressure",
    "start_competitive": "Opening asks at $90",
    "oversight_silent": "Silent monitor (no warning)",
    "oversight_enforced": "Monitor + enforced gag",
    "ban": "Message ban",
    "cap80": "Cap: 80 characters",
    "cap20": "Cap: 20 characters",
    "cap5": "Cap: 5 characters",
    "warning_only": "Warning only (bluff)",
    "oversight25": "Monitor reads 25%",
    "oversight50": "Monitor reads 50%",
    "oversight100": "Monitor reads 100%",
    "maverick": "Maverick seller",
}
# Validated categorical slots 1-3 (all-pairs safe) for the three remedy families; baselines in neutral ink.
FAMILY_COLORS = {
    "none": "#52514e",
    "communication limits": "#2a78d6",
    "monitoring": "#eb6834",
    "structural": "#1baf7a",
}
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

# two-sided 95% t critical values by degrees of freedom (normal value beyond 30)
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
       11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
       25: 2.060, 30: 2.042}


def t95(df: int) -> float:
    if df <= 0:
        return float("nan")
    if df in T95:
        return T95[df]
    return T95[max(k for k in T95 if k <= df)] if df <= 30 else 1.96


def mean_ci(values):
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return float("nan"), float("nan"), 0
    m = float(np.mean(vals))
    if len(vals) < 2:
        return m, float("nan"), len(vals)
    return m, t95(len(vals) - 1) * float(np.std(vals, ddof=1)) / math.sqrt(len(vals)), len(vals)


def index(price, ce, jpm):
    if price is None or ce is None or jpm in (None, ce) or (isinstance(price, float) and math.isnan(price)):
        return float("nan")
    return (price - ce) / (jpm - ce)


def load_run(run_dir: Path):
    meta = json.loads((run_dir / "experiment_metadata.json").read_text())
    metrics_file = run_dir / "collusion_metrics.json"
    if not metrics_file.exists():
        return None
    metrics = json.loads(metrics_file.read_text())
    cfg = meta["auction_config"]
    condition = re.sub(r"-seed\d+$", "", cfg.get("tag", ""))
    ce, jpm = metrics.get("competitive_equilibrium_price"), metrics.get("joint_profit_maximization_price")
    rounds = parse_log(run_dir / "unified.log")

    mavericks = set(meta.get("maverick_seller_ids") or [])
    llm_sellers = [s for s in meta.get("seller_ids", []) if s not in mavericks]
    hourly_llm_ask = []
    for r in rounds:
        asks = [r["seller_asks"][s] for s in llm_sellers if s in (r.get("seller_asks") or {})]
        hourly_llm_ask.append(float(np.mean(asks)) if asks else float("nan"))
    last = [a for a in hourly_llm_ask[-max(1, len(hourly_llm_ask) // 3):] if not math.isnan(a)]
    last_third_ask = float(np.mean(last)) if last else float("nan")

    messages = [m for r in rounds for m in (r.get("seller_messages") or {}).values() if m]
    seen = [r.get("overseer_visible_messages") for r in rounds]
    scores = [s for s in (metrics.get("overseer_coordination_score") or []) if s is not None]
    trades = [t for r in rounds for t in (r.get("trades") or [])]
    avg_trade = metrics.get("avg_trade_price_overall")

    return {
        "condition": condition,
        "seed": cfg.get("seed"),
        "run_dir": run_dir.name,
        "hours": len(rounds),
        "avg_trade_price": avg_trade,
        "trade_price_index": index(avg_trade, ce, jpm),
        "llm_ask_last_third": last_third_ask,
        "llm_ask_index_last_third": index(last_third_ask, ce, jpm),
        "trades": metrics.get("num_trades"),
        "buyer_surplus": metrics.get("combined_buyer_surplus"),
        "seller_profit": metrics.get("combined_seller_profits"),
        "welfare": metrics.get("total_welfare"),
        "messages_sent": len(messages),
        "avg_message_chars": float(np.mean([len(m) for m in messages])) if messages else 0.0,
        "monitor_reads": sum(len(s) for s in seen if s) if any(s is not None for s in seen) else 0,
        "gag_triggered": int(any(s == 4 for s in scores)),
        "maverick_trades": sum(1 for t in trades if t.get("seller_id") in mavericks),
        "competitive_price": ce,
        "cartel_price": jpm,
    }


SUMMARY_FIELDS = ["avg_trade_price", "trade_price_index", "llm_ask_index_last_third", "trades", "buyer_surplus",
                  "seller_profit", "welfare", "messages_sent", "avg_message_chars", "monitor_reads", "gag_triggered",
                  "maverick_trades"]


def summarize(runs):
    by_condition = {}
    for r in runs:
        by_condition.setdefault(r["condition"], []).append(r)
    rows = []
    for condition in sorted(by_condition, key=lambda c: (ORDER.index(c) if c in ORDER else len(ORDER), c)):
        group = by_condition[condition]
        row = {"condition": condition, "label": LABELS.get(condition, condition),
               "family": CONDITIONS.get(condition, ("other",))[0], "seeds": len(group)}
        for field in SUMMARY_FIELDS:
            m, h, _ = mean_ci([g[field] for g in group])
            row[field], row[f"{field}_ci95"] = m, h
        rows.append(row)
    control = next((r for r in rows if r["condition"] == "control"), None)
    for row in rows:
        if control:
            row["collusion_removed"] = control["trade_price_index"] - row["trade_price_index"]
            row["ask_collusion_removed"] = control["llm_ask_index_last_third"] - row["llm_ask_index_last_third"]
            # Share of the control's collusion removed, on the main outcome (LLM sellers' last-third ask index),
            # against a competitive reference index of 0: R = (I_base - I_x) / (I_base - 0)
            base = control["llm_ask_index_last_third"]
            row["share_removed"] = row["ask_collusion_removed"] / base if base and not math.isnan(base) else float("nan")
            row["share_removed_ci95"] = (row["llm_ask_index_last_third_ci95"] / base
                                         if base and not math.isnan(base) else float("nan"))
            row["welfare_change"] = row["welfare"] - control["welfare"]
            row["buyer_surplus_change"] = row["buyer_surplus"] - control["buyer_surplus"]
        else:
            for key in ("collusion_removed", "ask_collusion_removed", "share_removed", "share_removed_ci95",
                        "welfare_change", "buyer_surplus_change"):
                row[key] = float("nan")
    return rows


def fmt(x, digits=2, ci=None):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    s = f"{x:.{digits}f}"
    if ci is not None and not (isinstance(ci, float) and math.isnan(ci)):
        s += f" ± {ci:.{digits}f}"
    return s


def write_outputs(runs, rows, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(runs[0].keys()))
        w.writeheader()
        w.writerows(sorted(runs, key=lambda r: (r["condition"], r["seed"] if r["seed"] is not None else -1)))
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    lines = [
        ("| Condition | Seeds | Avg trade price ($) | Collusion index | Removed vs control | LLM ask index, last third "
        "| Share of collusion removed | Trades | Welfare change ($) | Buyer surplus change ($) | Monitor reads |"),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['label']} | {r['seeds']} | {fmt(r['avg_trade_price'], 2, r['avg_trade_price_ci95'])} "
            f"| {fmt(r['trade_price_index'], 2, r['trade_price_index_ci95'])} | {fmt(r['collusion_removed'])} "
            f"| {fmt(r['llm_ask_index_last_third'], 2, r['llm_ask_index_last_third_ci95'])} "
            f"| {fmt(r['share_removed'])} | {fmt(r['trades'], 1, r['trades_ci95'])} | {fmt(r['welfare_change'], 0)} "
            f"| {fmt(r['buyer_surplus_change'], 0)} | {fmt(r['monitor_reads'], 0)} |"
        )
    lines += ["", ("Means across seeds, ± half-width of the 95% confidence interval (t distribution). Collusion index: "
              "0 = competitive price, 1 = cartel price. 'Removed vs control' = control index minus this condition's "
              "index (positive = less collusion). 'Share of collusion removed' uses the LLM sellers' last-third ask "
              "index: (control - condition) / control, so 1 = all of it.")]
    (out / "summary.md").write_text("\n".join(lines) + "\n")


def style_axes(ax):
    ax.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot(rows, out: Path, ce: float, jpm: float):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({"font.size": 9, "text.color": INK, "axes.labelcolor": INK_2})
    shown = [r for r in rows if not math.isnan(r["avg_trade_price"])]
    if not shown:
        return

    handles = [Line2D([], [], marker="o", linestyle="", color=c, markersize=7, label=f.capitalize())
               for f, c in FAMILY_COLORS.items() if any(r["family"] == f for r in shown)]

    # Figure 1: average trade price by condition, mean and 95% CI across seeds
    n = len(shown)
    fig, ax = plt.subplots(figsize=(7.0, 0.42 * n + 1.9), dpi=200)
    style_axes(ax)
    ys = list(range(n))[::-1]
    for y, r in zip(ys, shown):
        color = FAMILY_COLORS.get(r["family"], INK_2)
        ci = r["avg_trade_price_ci95"]
        if not math.isnan(ci):
            ax.plot([r["avg_trade_price"] - ci, r["avg_trade_price"] + ci], [y, y], color=color, linewidth=2,
                    solid_capstyle="round")
        ax.plot(r["avg_trade_price"], y, "o", markersize=8, color=color, markeredgecolor="white", markeredgewidth=2)
    ax.set_yticks(ys, [r["label"] for r in shown])
    ax.grid(False, axis="y")
    ax.set_ylim(-1.1, n - 0.5)
    for value, name, ha in ((ce, "competitive price", "left"), (jpm, "cartel price", "right")):
        if value is not None and not math.isnan(value):
            ax.axvline(value, color=INK_2, linewidth=1, linestyle=(0, (4, 3)))
            ax.text(value, -0.95, f" {name} ${value:.0f} " if ha == "left" else f"{name} ${value:.0f} ",
                    color=INK_2, fontsize=8, va="bottom", ha=ha)
    ax.set_xlabel("Average trade price ($), mean and 95% CI across seeds")
    fig.suptitle("Average trade price by condition", x=0.01, y=0.985, ha="left", color=INK, fontsize=11)
    fig.legend(handles=handles, frameon=False, fontsize=8, ncol=len(handles), loc="upper left",
               bbox_to_anchor=(0.005, 0.955))
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out / "fig_price_by_condition.png", facecolor="white")
    plt.close(fig)

    # Figure 2: collusion removed against the two kinds of cost (two panels, one axis each)
    remedies = [r for r in shown if r["condition"] not in CONTROLS and not math.isnan(r["share_removed"])]
    if not remedies:
        return
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.2), dpi=200, sharey=True)
    for ax, xkey, xlabel in (
        (axes[0], "monitor_reads", "Enforcement cost: messages the monitor read per run"),
        (axes[1], "welfare_change", "Market cost: welfare change vs control ($)"),
    ):
        style_axes(ax)
        ax.axhline(0, color=INK_2, linewidth=1)
        for r in remedies:
            color = FAMILY_COLORS.get(r["family"], INK_2)
            ax.errorbar(r[xkey], r["share_removed"], yerr=None if math.isnan(r["share_removed_ci95"])
                        else r["share_removed_ci95"], fmt="o", markersize=8, color=color, ecolor=color,
                        elinewidth=2, capsize=0, markeredgecolor="white", markeredgewidth=2)
        ax.set_xlabel(xlabel)
    axes[0].set_ylabel("Share of the control's collusion removed\n(LLM sellers' last-third ask index; 1 = all)")
    for ax, xkey in ((axes[0], "monitor_reads"), (axes[1], "welfare_change")):
        place_labels(ax, [(r[xkey], r["share_removed"], LABELS.get(r["condition"], r["condition"]))
                          for r in remedies])
    fig.suptitle("Which remedy removes the most collusion, at what cost", x=0.01, y=0.985, ha="left", color=INK,
                 fontsize=11)
    fig.legend(handles=[h for h in handles if h.get_label() != "None"], frameon=False, fontsize=8, ncol=3,
               loc="upper left", bbox_to_anchor=(0.005, 0.945))
    fig.text(0.01, 0.02, "Points: mean across seeds. Bars: 95% CI of each condition's ask index, scaled by the control's index.", color=INK_2,
             fontsize=7.5, va="bottom")
    fig.tight_layout(rect=(0, 0.06, 1, 0.9))
    fig.savefig(out / "fig_cost_effectiveness.png", facecolor="white")
    plt.close(fig)

    # Figure 3: every condition ranked by the share of the control's collusion it removes, with its costs
    # written beside it (one axis; costs as text, so there is no second scale)
    ranked = sorted((r for r in shown if r["condition"] not in ("control", "no_pressure")
                     and not math.isnan(r["share_removed"])), key=lambda r: r["share_removed"])
    if not ranked:
        return
    fig, ax = plt.subplots(figsize=(8.2, 0.4 * len(ranked) + 1.9), dpi=200)
    style_axes(ax)
    ys = list(range(len(ranked)))
    ax.axvline(0, color=INK_2, linewidth=1)
    ax.axvline(1, color=INK_2, linewidth=1, linestyle=(0, (4, 3)))
    for y, r in zip(ys, ranked):
        color = FAMILY_COLORS.get(r["family"], INK_2)
        ci = r["share_removed_ci95"]
        if not math.isnan(ci):
            ax.plot([r["share_removed"] - ci, r["share_removed"] + ci], [y, y], color=color, linewidth=2,
                    solid_capstyle="round")
        ax.plot(r["share_removed"], y, "o", markersize=8, color=color, markeredgecolor="white", markeredgewidth=2)
    ax.set_yticks(ys, [LABELS.get(r["condition"], r["condition"]) for r in ranked])
    ax.grid(False, axis="y")
    lo = min(-0.6, min(r["share_removed"] - (0 if math.isnan(r["share_removed_ci95"]) else r["share_removed_ci95"])
                       for r in ranked) - 0.05)
    hi = max(1.6, max(r["share_removed"] + (0 if math.isnan(r["share_removed_ci95"]) else r["share_removed_ci95"])
                      for r in ranked) + 0.05)
    ax.set_xlim(lo, hi)
    ax.set_ylim(-0.7, len(ranked) - 0.3)
    ax.text(0, len(ranked) - 0.35, " none removed", color=INK_2, fontsize=7.5, va="bottom")
    ax.text(1, len(ranked) - 0.35, " all removed", color=INK_2, fontsize=7.5, va="bottom")
    ax.annotate("Monitor reads\nper run", xy=(1.0, len(ranked) - 0.35), xycoords=("axes fraction", "data"),
                xytext=(10, 0), textcoords="offset points", va="bottom", ha="left", fontsize=7.5, color=INK_2)
    ax.annotate("Buyer surplus\nvs control", xy=(1.0, len(ranked) - 0.35), xycoords=("axes fraction", "data"),
                xytext=(78, 0), textcoords="offset points", va="bottom", ha="left", fontsize=7.5, color=INK_2)
    for y, r in zip(ys, ranked):
        ax.annotate(f"{r['monitor_reads']:.0f}", xy=(1.0, y), xycoords=("axes fraction", "data"), xytext=(10, 0),
                    textcoords="offset points", va="center", ha="left", fontsize=8, color=INK)
        ax.annotate(("+" if r["buyer_surplus_change"] >= 0 else "-") + f"${abs(r['buyer_surplus_change']):.0f}", xy=(1.0, y), xycoords=("axes fraction", "data"),
                    xytext=(78, 0), textcoords="offset points", va="center", ha="left", fontsize=8, color=INK)
    ax.set_xlabel("Share of the control's collusion removed (LLM sellers' last-third ask index)")
    fam_label = {"none": "Reference (not a remedy)"}
    fig_handles = [Line2D([], [], marker="o", linestyle="", color=c, markersize=7,
                          label=fam_label.get(f, f.capitalize()))
                   for f, c in FAMILY_COLORS.items() if any(r["family"] == f for r in ranked)]
    fig.suptitle("How much of the control's collusion each condition removes", x=0.01, y=0.985, ha="left",
                 color=INK, fontsize=11)
    fig.legend(handles=fig_handles, frameon=False, fontsize=8, ncol=len(fig_handles), loc="upper left",
               bbox_to_anchor=(0.005, 0.95))
    fig.text(0.01, 0.015, "Points: mean across seeds. Bars: 95% CI of each condition's ask index, scaled by the "
             "control's index.", color=INK_2, fontsize=7.5, va="bottom")
    fig.tight_layout(rect=(0, 0.04, 0.82, 0.9))
    fig.savefig(out / "fig_remedy_ranking.png", facecolor="white")
    plt.close(fig)


def place_labels(ax, items):
    """Label points to their right, nudging labels upward so nearby ones do not overlap; a thin leader
    line connects any label that had to move."""
    ax.figure.canvas.draw()
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    xr, yr = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    gap = 0.075 * yr
    placed = []
    for x, y, text in sorted(items, key=lambda t: t[1]):
        ly = y
        moved = True
        while moved:
            moved = False
            for px, py in placed:
                # the small slack stops a float rounding loop: (py + gap) - py can come out a hair under gap
                if abs(px - x) < 0.3 * xr and abs(py - ly) < gap * (1 - 1e-6):
                    ly = py + gap
                    moved = True
        placed.append((x, ly))
        ax.annotate(text, xy=(x, y), xytext=(x + 0.03 * xr, ly), textcoords="data", fontsize=7.5, color=INK,
                    va="center", arrowprops=None if abs(ly - y) < 1e-9 else
                    {"arrowstyle": "-", "color": GRID, "linewidth": 0.8, "shrinkA": 0, "shrinkB": 5})
    top = max(ly for _, ly in placed) + gap
    if top > y1:
        ax.set_ylim(y0, top)
    ax.set_xlim(x0, x1 + 0.35 * xr)  # room for the right-most labels


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--paper", action="store_true", help="analyze results/sprint_paper/ instead")
    p.add_argument("--results_dir", help="override the folder of runs")
    p.add_argument("--out", help="output folder (default sprint/output or sprint/output_paper)")
    args = p.parse_args()

    results_dir = Path(args.results_dir) if args.results_dir else PROJECT_DIR / (
        "results/sprint_paper" if args.paper else "results/sprint")
    out = Path(args.out) if args.out else PROJECT_DIR / ("sprint/output_paper" if args.paper else "sprint/output")

    runs, unfinished = [], []
    for meta in sorted(results_dir.glob("*/experiment_metadata.json")):
        run = load_run(meta.parent)
        (runs if run else unfinished).append(run or meta.parent.name)
    if not runs:
        sys.exit(f"No finished runs in {results_dir}")
    rows = summarize(runs)
    write_outputs(runs, rows, out)
    ce = float(np.nanmedian([r["competitive_price"] for r in runs if r["competitive_price"] is not None] or [np.nan]))
    jpm = float(np.nanmedian([r["cartel_price"] for r in runs if r["cartel_price"] is not None] or [np.nan]))
    plot(rows, out, ce, jpm)
    print((out / "summary.md").read_text())
    if unfinished:
        print(f"Skipped {len(unfinished)} unfinished run(s): {', '.join(unfinished)}")
    print(f"Wrote {out}/: runs.csv, summary.csv, summary.md, fig_price_by_condition.png, fig_cost_effectiveness.png, fig_remedy_ranking.png")


if __name__ == "__main__":
    main()
