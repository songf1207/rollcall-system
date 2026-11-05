"""从实测 JSON 生成可用于博客的性能图，不内置演示数字。"""
from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent


def main():
    report = json.loads((ROOT / "docs/benchmark.json").read_text(encoding="utf-8"))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), layout="constrained")
    for ax, group, fields, labels, title in [
        (axes[0], "batch", ("naive_ms", "ares_ms"), ("Repeated weighted draws", "One batch with A-Res"), "Batch: draw all n students"),
        (axes[1], "single", ("sort_ms", "min_ms"), ("Full sort", "Minimum scan"), "Single: draw one student"),
    ]:
        rows = report[group]
        for field, label in zip(fields, labels):
            ax.plot([r["n"] for r in rows], [r[field] for r in rows], marker="o", label=label)
        ax.set(title=title, xlabel="Candidates (n)", ylabel="Mean elapsed time (ms)")
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    row = report["import"]
    bars = axes[2].bar(["Per-row commit", "Batch commit"], [row["per_row_ms"], row["batch_ms"]], color=["#dc8c49", "#367ac1"])
    axes[2].bar_label(bars, fmt="%.1f ms", padding=3)
    axes[2].set(title="Insert 1,000 rows: temporary SQLite", ylabel="Elapsed time (ms)")
    axes[2].set_ylim(0, row["per_row_ms"] * 1.18)
    fig.suptitle(f"Local measurements | Python {report['python']} | Not online API latency", fontsize=12)
    fig.savefig(ROOT / "docs/performance.png", dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    main()
