"""One-off theme correction pass over the hand-labelled demo corpus.

Changes only the `theme` column of demo_data/labelled.csv.
"""

import sys
from pathlib import Path

import pandas as pd

CSV_PATH = Path(__file__).resolve().parent / "labelled.csv"

ALLOWED_THEMES = {
    "Comeback celebration",
    "Bring back other variants",
    "Not like the old recipe",
    "Spice level",
    "Where to buy it",
    "Jingle and the ad itself",
    "Personal memory",
    "Other",
}

# ponytail: one-off correction table for one frozen corpus, safe to delete
# once the corrected labelled.csv is committed.
# positional row index -> (expected current theme, corrected theme)
CHANGES = {
    0: ("Where to buy it", "Comeback celebration"),
    11: ("Bring back other variants", "Not like the old recipe"),
    17: ("Other", "Not like the old recipe"),
    32: ("Other", "Jingle and the ad itself"),
    33: ("Comeback celebration", "Other"),
    41: ("Comeback celebration", "Bring back other variants"),
    49: ("Where to buy it", "Personal memory"),
    57: ("Other", "Jingle and the ad itself"),
    63: ("Bring back other variants", "Spice level"),
    71: ("Personal memory", "Jingle and the ad itself"),
    73: ("Other", "Jingle and the ad itself"),
    84: ("Comeback celebration", "Not like the old recipe"),
    101: ("Bring back other variants", "Comeback celebration"),
    130: ("Not like the old recipe", "Personal memory"),
    135: ("Other", "Not like the old recipe"),
    137: ("Comeback celebration", "Other"),
    142: ("Not like the old recipe", "Jingle and the ad itself"),
    143: ("Spice level", "Jingle and the ad itself"),
    144: ("Not like the old recipe", "Jingle and the ad itself"),
    149: ("Comeback celebration", "Other"),
    177: ("Spice level", "Jingle and the ad itself"),
    180: ("Other", "Jingle and the ad itself"),
}


def main():
    df = pd.read_csv(CSV_PATH, dtype=str, encoding="utf-8", keep_default_na=False)

    original_columns = list(df.columns)
    original_rows = len(df)

    if len(df) != 181:
        raise SystemExit(f"expected 181 rows, found {len(df)}")

    if "theme" not in df.columns:
        raise SystemExit("labelled.csv has no theme column")

    for _, corrected in CHANGES.values():
        if corrected not in ALLOWED_THEMES:
            raise SystemExit(f"corrected theme not in ALLOWED_THEMES: {corrected!r}")

    mismatches = []
    for index, (expected, _corrected) in CHANGES.items():
        found = df.at[index, "theme"]
        if found != expected:
            mismatches.append(f"row {index}: expected {expected!r}, found {found!r}")

    if mismatches:
        for line in mismatches:
            print(line)
        raise SystemExit("preconditions failed, nothing written")

    for index, (_expected, corrected) in CHANGES.items():
        df.at[index, "theme"] = corrected

    if list(df.columns) != original_columns:
        raise SystemExit("columns changed during processing")
    if len(df) != original_rows:
        raise SystemExit("row count changed during processing")

    unknown = sorted(set(df["theme"]) - ALLOWED_THEMES)
    if unknown:
        raise SystemExit(f"unknown theme values after apply: {unknown}")

    df.to_csv(CSV_PATH, index=False, encoding="utf-8", lineterminator="\n")

    print(f"applied {len(CHANGES)} theme corrections")
    counts = df["theme"].value_counts()
    for theme, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{theme}: {count}")


if __name__ == "__main__":
    main()
