"""Search convergence plot: best-so-far and current objective versus elapsed time and iteration."""
from typing import List

import matplotlib.pyplot as plt


def save_convergence(path: str, history: List[dict], title: str, objective: str,
                     show: bool = False) -> None:
    cur = [h["current_objective"] for h in history]
    best = [h["best_objective"] for h in history]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for ax, key, label in ((axes[0], "elapsed_s", "Elapsed time (s)"),
                           (axes[1], "iteration", "Iteration")):
        x = [h[key] for h in history]
        ax.plot(x, cur, color="#9ecae1", linewidth=0.8, label="current")
        ax.step(x, best, where="post", color="#d62728", linewidth=1.6, label="best so far")
        ax.set_xlabel(label)
        ax.set_ylabel(objective)
        ax.grid(linestyle=":", alpha=0.6)
    axes[0].legend(fontsize=8)
    fig.suptitle(title, fontsize=10, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    if show:
        plt.show()
    plt.close(fig)
