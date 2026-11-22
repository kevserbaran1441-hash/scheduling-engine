"""Gantt çizimi. Çizim, schedule.csv'den okunan satırlarla yapılır; çizelge yeniden hesaplanmaz.

Her makine bir şerit (M1..Mm). Aynı iş her grafikte aynı rengi alır. İşler arasındaki boş
(idle) aralıklar (e.g. waiting for a release date) hafif taramalı gösterilir; makinenin işi bittikten
sonraki boşluk çizilmez. Zaman ekseni birimli etiketlenir.
"""
import math
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from .models import Instance, ScheduleRow

IDLE_STYLE = dict(color="#f4f4f4", edgecolor="#9a9a9a", hatch="///", linewidth=0.5)


def make_title(inst: Instance, method: str, metrics: dict, objective: str) -> str:
    """Stacked title: method / main metrics / instance and selected objective."""
    f = lambda v: "-" if v is None else str(v)
    return ("%s\n"
            "Cmax=%s   \u03a3Cj=%s   \u03a3wjCj=%s   \u03a3Tj=%s\n"
            "Instance: %s  |  Objective: %s = %s"
            % (method, f(metrics["makespan"]), f(metrics["total_completion"]),
               f(metrics["weighted_completion"]), f(metrics["total_tardiness"]),
               inst.name, objective, f(metrics[objective])))


def _height(inst: Instance) -> float:
    return 1.2 + 0.6 * inst.machines


def job_colors(inst: Instance) -> Dict[str, tuple]:
    cmap = plt.get_cmap("tab20")
    return {j.job_id: cmap(i % 20) for i, j in enumerate(inst.jobs)}


def draw_gantt(ax, inst: Instance, rows: List[ScheduleRow], title: str,
               xmax: float, colors: Dict[str, tuple], time_unit: str) -> bool:
    """Çizer; işler arasında boşluk çizildiyse True döner."""
    mids = inst.machine_ids()
    ypos = {m: i for i, m in enumerate(mids)}
    idle_drawn = False
    for m in mids:
        t = 0
        for r in sorted((r for r in rows if r.machine_id == m), key=lambda r: r.start_time):
            if r.start_time > t:
                ax.barh(ypos[m], r.start_time - t, left=t, height=0.6, **IDLE_STYLE)
                idle_drawn = True
            ax.barh(ypos[m], r.end_time - r.start_time, left=r.start_time, height=0.6,
                    color=colors[r.job_id], edgecolor="black")
            ax.text((r.start_time + r.end_time) / 2, ypos[m], r.job_id,
                    ha="center", va="center", fontsize=8, fontweight="bold")
            t = r.end_time
    ax.set_yticks(range(len(mids)))
    ax.set_yticklabels(mids)
    ax.invert_yaxis()
    ax.set_xlim(0, xmax)
    ax.set_xlabel("Time (%s)" % time_unit)
    head, _, info = title.partition("\n")
    n_info = info.count("\n") + 1 if info else 0
    ax.set_title(head, fontsize=10, fontweight="bold", pad=12 + 13 * n_info)
    if info:
        ax.annotate(info, xy=(0.5, 1), xycoords="axes fraction", xytext=(0, 4),
                    textcoords="offset points", ha="center", va="bottom",
                    fontsize=8.5, linespacing=1.25)
    ax.grid(axis="x", linestyle=":", alpha=0.6)
    return idle_drawn


def _idle_legend(fig):
    fig.legend(handles=[Patch(label="idle", facecolor="#f4f4f4",
                              edgecolor="#9a9a9a", hatch="///")],
               loc="lower right", fontsize=8)


def save_gantt(path: str, inst: Instance, rows: List[ScheduleRow], title: str,
               time_unit: str, show: bool = False) -> None:
    xmax = max(r.end_time for r in rows) * 1.03
    fig, ax = plt.subplots(figsize=(8, _height(inst)))
    if draw_gantt(ax, inst, rows, title, xmax, job_colors(inst), time_unit):
        _idle_legend(fig)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    if show:
        plt.show()
    plt.close(fig)


def save_comparison(path: str, inst: Instance, entries: List[Tuple[str, List[ScheduleRow]]],
                    time_unit: str, show: bool = False) -> None:
    """entries: [(başlık, rows)] - kurallar aynı zaman ekseninde yan yana."""
    n = len(entries)
    ncols = 2 if n > 1 else 1
    nrows = math.ceil(n / ncols)
    xmax = max(r.end_time for _, rows in entries for r in rows) * 1.03
    colors = job_colors(inst)
    fig, axes = plt.subplots(nrows, ncols, squeeze=False,
                             figsize=(7 * ncols, _height(inst) * nrows))
    flat = axes.flatten()
    any_idle = False
    for ax, (title, rows) in zip(flat, entries):
        any_idle |= draw_gantt(ax, inst, rows, title, xmax, colors, time_unit)
    for ax in flat[n:]:
        ax.axis("off")
    if any_idle:
        _idle_legend(fig)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    if show:
        plt.show()
    plt.close(fig)