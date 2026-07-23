"""
Stage 5a - draw the figures the report embeds.

Two charts, both written as PNG into the output folder so the markdown
can reference them and so they survive a copy-paste into a deck.

Deliberately plain: no gridlines, no chart junk, colour used only to
carry meaning (green = arrived, amber = partial, red = did not arrive).
"""

import os
import matplotlib
matplotlib.use("Agg")          # render to file, never open a window
import matplotlib.pyplot as plt

INK = "#1c1c1c"
MUTED = "#6b6660"
GREEN = "#4b7f52"
AMBER = "#c9a227"
RED = "#b3452f"
GREY = "#b8b2a8"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "text.color": INK,
    "axes.labelcolor": INK,
    "xtick.color": INK,
    "ytick.color": INK,
})


def _band(pct):
    """Colour by whether the idea arrived at all."""
    if pct >= 5:
        return GREEN
    if pct >= 2:
        return AMBER
    return RED


def transfer_chart(transfer_table, path):
    """
    Horizontal bars, one per idea, sorted high to low.

    This is the chart that carries the argument: it shows which of the
    things the video pushed actually entered the conversation.
    """
    if transfer_table is None or transfer_table.empty:
        return None

    data = transfer_table.sort_values("echoed_pct", ascending=False)
    labels = [f"{row.group}  \u2014  {row.point}"
              for row in data.itertuples()]
    values = list(data["echoed_pct"])
    colours = [_band(v) for v in values]

    height = max(2.2, 0.42 * len(labels) + 1.0)
    fig, ax = plt.subplots(figsize=(9.4, height))
    positions = range(len(labels))

    ax.barh(list(positions), values, color=colours, height=0.6)
    for i, value in enumerate(values):
        ax.text(value + max(values) * 0.015, i,
                f"{value:.1f}%" if value else "0%",
                va="center", fontsize=8.4, color="#4a4640")

    ax.set_yticks(list(positions))
    ax.set_yticklabels(labels, fontsize=8.6)
    ax.invert_yaxis()
    ax.set_xlim(0, max(max(values) * 1.25, 1))
    ax.set_xticks([])
    ax.tick_params(axis="y", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xlabel("share of that conversation which echoed the idea",
                  fontsize=7.8, color=MUTED)

    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def theme_chart(theme_table, path):
    """Stacked bars: what each conversation was about."""
    if theme_table is None or theme_table.empty:
        return None

    palette = ["#2f4858", "#b3452f", "#c9822b", "#4b7f52", "#5a7d9a",
               "#8c7ba6", "#7a6a5c", GREY]

    height = max(2.0, 0.55 * len(theme_table) + 1.4)
    fig, ax = plt.subplots(figsize=(9.4, height))

    left = [0.0] * len(theme_table)
    for i, column in enumerate(theme_table.columns):
        values = list(theme_table[column])
        ax.barh(list(theme_table.index), values, left=left,
                label=column, color=palette[i % len(palette)],
                height=0.5, edgecolor="white", linewidth=1.2)
        for j, value in enumerate(values):
            if value >= 7:
                ax.text(left[j] + value / 2, j, f"{value:.0f}",
                        ha="center", va="center", color="white",
                        fontsize=8, fontweight="bold")
        left = [a + b for a, b in zip(left, values)]

    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0", "25", "50", "75", "100%"], fontsize=8)
    ax.tick_params(axis="y", length=0, labelsize=9.5)
    ax.tick_params(axis="x", length=0, colors=MUTED)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12),
              ncol=min(4, len(theme_table.columns)), frameon=False,
              fontsize=8)

    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def run(theme_table, transfer_table, output_dir):
    """Draw both, return the filenames that were actually written."""
    written = {}
    transfer_path = os.path.join(output_dir, "05_signal_transfer.png")
    theme_path = os.path.join(output_dir, "05_theme_mix.png")

    if transfer_chart(transfer_table, transfer_path):
        written["transfer"] = os.path.basename(transfer_path)
        print(f"    chart: {written['transfer']}")
    if theme_chart(theme_table, theme_path):
        written["theme"] = os.path.basename(theme_path)
        print(f"    chart: {written['theme']}")
    return written