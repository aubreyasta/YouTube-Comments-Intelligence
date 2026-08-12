"""
Evaluation harness for the Indonesian-English Sentiment and Emotion
classifier candidates (see CHANGELOG.md, Phase 1 item 1: multilingual
scope is Indonesian, English, and mixed Indonesian-English comments;
Macro F1 is the selection metric, accuracy is secondary).

This script does not call or download any model. It scores a predictions
JSONL against a labelled JSONL. Model inference is a separate, later step;
run today's benchmark by writing a candidate's outputs to a predictions
file and pointing --predictions at it.

Candidate model IDs (reference only, not downloaded or executed here):
  sentiment (single, multilingual): cardiffnlp/twitter-xlm-roberta-base-sentiment
  sentiment (routed by detected language):
    id -> w11wo/indonesian-roberta-base-sentiment-classifier
    en -> cardiffnlp/twitter-roberta-base-sentiment-latest
  emotion (multilingual): MilaNLProc/xlm-emo-t
  emotion (diagnostic only, language-specific):
    id -> StevenLimcorn/indonesian-roberta-base-emotion-classifier
    en -> j-hartmann/emotion-english-distilroberta-base

Labelled input schema (JSONL, one object per line):
  id         string, unique
  text       string, the comment text
  language   one of "id", "en", "mixed"
  sentiment  string label
  emotion    string label

Predictions file: same schema, sentiment/emotion are the model's
predicted labels for the same ids.

Run:
  python tests/bench_classifiers.py --labels LABELS.jsonl --predictions PREDS.jsonl
  python tests/bench_classifiers.py --self-check
"""

import argparse
import json
import sys
from collections import Counter, defaultdict

REQUIRED_FIELDS = ("id", "text", "language", "sentiment", "emotion")
VALID_LANGUAGES = {"id", "en", "mixed"}


# --------------------------------------------------------------- loading

def load_jsonl(path):
    """Read a labelled or predictions JSONL file, validating the schema."""
    records = {}
    with open(path, encoding="utf-8-sig") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_num}: invalid JSON: {exc}")
            missing = [field for field in REQUIRED_FIELDS if field not in row]
            if missing:
                raise ValueError(f"{path}:{line_num}: missing fields {missing}")
            if row["language"] not in VALID_LANGUAGES:
                raise ValueError(
                    f"{path}:{line_num}: language must be one of "
                    f"{sorted(VALID_LANGUAGES)}, got {row['language']!r}")
            if row["id"] in records:
                raise ValueError(f"{path}:{line_num}: duplicate id {row['id']!r}")
            records[row["id"]] = row
    if not records:
        raise ValueError(f"{path}: no rows")
    return records


# ------------------------------------------------------------------ metrics

def confusion_counts(y_true, y_pred):
    """dict[(true_label, pred_label)] -> count, over paired label lists."""
    counts = Counter(zip(y_true, y_pred))
    return dict(counts)


def per_label_scores(y_true, y_pred):
    """
    Precision, recall, F1 per label (one-vs-rest), plus support (count of
    true instances). Returns dict[label] -> {"precision", "recall", "f1", "support"}.
    """
    labels = sorted(set(y_true) | set(y_pred))
    scores = {}
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        support = sum(1 for t in y_true if t == label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        scores[label] = {"precision": precision, "recall": recall,
                          "f1": f1, "support": support}
    return scores


def macro_f1(y_true, y_pred):
    """Unweighted mean of per-label F1. Labels with zero support still
    count (their F1 is 0 unless never predicted and never true), matching
    scikit-learn's macro average over the union of observed labels."""
    scores = per_label_scores(y_true, y_pred)
    if not scores:
        return 0.0
    return sum(s["f1"] for s in scores.values()) / len(scores)


def accuracy(y_true, y_pred):
    if not y_true:
        return 0.0
    return sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true)


def evaluate_field(labels, predictions, field):
    """
    Full report for one label field ("sentiment" or "emotion") across the
    matched id set: macro F1, accuracy, per-label scores, confusion counts,
    and the same three broken out per language stratum.
    """
    ids = sorted(set(labels) & set(predictions))
    y_true = [labels[i][field] for i in ids]
    y_pred = [predictions[i][field] for i in ids]

    report = {
        "n": len(ids),
        "macro_f1": macro_f1(y_true, y_pred),
        "accuracy": accuracy(y_true, y_pred),
        "per_label": per_label_scores(y_true, y_pred),
        "confusion": confusion_counts(y_true, y_pred),
        "by_language": {},
    }

    by_lang = defaultdict(list)
    for i in ids:
        by_lang[labels[i]["language"]].append(i)
    for lang, lang_ids in sorted(by_lang.items()):
        lt = [labels[i][field] for i in lang_ids]
        lp = [predictions[i][field] for i in lang_ids]
        report["by_language"][lang] = {
            "n": len(lang_ids),
            "macro_f1": macro_f1(lt, lp),
            "accuracy": accuracy(lt, lp),
        }
    return report


def evaluate(labels, predictions):
    label_ids, pred_ids = set(labels), set(predictions)
    missing = label_ids - pred_ids
    extra = pred_ids - label_ids
    matched = label_ids & pred_ids
    if not matched:
        raise ValueError("no overlapping ids between labels and predictions")
    return {
        "matched": len(matched),
        "missing_predictions": len(missing),
        "extra_predictions": len(extra),
        "sentiment": evaluate_field(labels, predictions, "sentiment"),
        "emotion": evaluate_field(labels, predictions, "emotion"),
    }


# ------------------------------------------------------------------ printing

def print_field_report(name, report):
    print(f"\n{name}: n={report['n']}  macro_f1={report['macro_f1']:.3f}  "
          f"accuracy={report['accuracy']:.3f}")
    print(f"  per-label (label: precision/recall/f1/support):")
    for label, s in sorted(report["per_label"].items()):
        print(f"    {label:20s} {s['precision']:.3f} / {s['recall']:.3f} "
              f"/ {s['f1']:.3f} / n={s['support']}")
    print(f"  by language stratum (lang: n, macro_f1, accuracy):")
    for lang, s in sorted(report["by_language"].items()):
        print(f"    {lang:8s} n={s['n']:<6d} macro_f1={s['macro_f1']:.3f} "
              f"accuracy={s['accuracy']:.3f}")
    print(f"  confusion counts (true -> pred: count):")
    for (true, pred), count in sorted(report["confusion"].items()):
        marker = "" if true == pred else "  <-- miss"
        print(f"    {true} -> {pred}: {count}{marker}")


def print_report(result):
    print(f"matched={result['matched']}  "
          f"missing_predictions={result['missing_predictions']}  "
          f"extra_predictions={result['extra_predictions']}")
    print_field_report("sentiment", result["sentiment"])
    print_field_report("emotion", result["emotion"])


# ------------------------------------------------------------------ self-check

def _self_check():
    """
    Synthetic imbalanced label set proving macro F1 exposes majority-only
    prediction while accuracy hides it. 1 negative : 9 positive comments;
    a model that always predicts "positive" scores high accuracy but zero
    recall on the minority class, which macro F1 must punish and accuracy
    must not.
    """
    y_true = ["negative"] + ["positive"] * 9
    y_pred_majority_only = ["positive"] * 10

    acc = accuracy(y_true, y_pred_majority_only)
    mf1 = macro_f1(y_true, y_pred_majority_only)
    assert acc == 0.9, f"expected accuracy 0.9, got {acc}"
    # macro F1 over {"negative", "positive"}: negative has 0 tp/0 fp -> f1=0,
    # positive has precision 0.9, recall 1.0 -> f1 ~ 0.947. mean ~ 0.474.
    assert mf1 < 0.5, f"macro F1 should expose the majority-only failure, got {mf1}"
    assert mf1 < acc, "macro F1 must be lower than accuracy for this case"

    # A perfect predictor scores 1.0 on both.
    assert accuracy(y_true, y_true) == 1.0
    assert macro_f1(y_true, y_true) == 1.0

    # Per-label scores: negative has zero recall under majority-only.
    scores = per_label_scores(y_true, y_pred_majority_only)
    assert scores["negative"]["recall"] == 0.0
    assert scores["negative"]["support"] == 1
    assert scores["positive"]["support"] == 9

    # Confusion counts sum to n.
    counts = confusion_counts(y_true, y_pred_majority_only)
    assert sum(counts.values()) == 10
    assert counts[("negative", "positive")] == 1
    assert counts[("positive", "positive")] == 9

    # evaluate_field end-to-end, including the by-language stratum split.
    labels = {
        "c1": {"language": "id", "sentiment": "negative", "emotion": "anger"},
        "c2": {"language": "en", "sentiment": "positive", "emotion": "joy"},
        "c3": {"language": "mixed", "sentiment": "positive", "emotion": "joy"},
    }
    predictions = {
        "c1": {"sentiment": "positive", "emotion": "anger"},
        "c2": {"sentiment": "positive", "emotion": "joy"},
        "c3": {"sentiment": "positive", "emotion": "neutral"},
    }
    field_report = evaluate_field(labels, predictions, "sentiment")
    assert field_report["n"] == 3
    assert field_report["accuracy"] == 2 / 3
    assert field_report["by_language"]["id"]["accuracy"] == 0.0
    assert field_report["by_language"]["en"]["accuracy"] == 1.0
    assert field_report["by_language"]["mixed"]["accuracy"] == 1.0

    emotion_report = evaluate_field(labels, predictions, "emotion")
    assert emotion_report["per_label"]["neutral"]["precision"] == 0.0
    assert emotion_report["per_label"]["joy"]["support"] == 2

    # evaluate() end-to-end: mismatched id sets are reported, not silently
    # dropped, and scoring runs only over the intersection.
    predictions_partial = dict(predictions)
    del predictions_partial["c3"]
    predictions_partial["c4_unlabelled"] = {"sentiment": "positive", "emotion": "joy"}
    full_labels = dict(labels)
    result = evaluate(full_labels, predictions_partial)
    assert result["matched"] == 2
    assert result["missing_predictions"] == 1
    assert result["extra_predictions"] == 1

    print("self-check: PASS (macro F1 exposed majority-only prediction; "
          f"accuracy={acc:.3f} vs macro_f1={mf1:.3f})")


# ------------------------------------------------------------------ CLI

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Score Indonesian-English Sentiment and Emotion classifier "
            "predictions against labelled data. Macro F1 is primary "
            "(selection metric per CHANGELOG.md), accuracy is secondary. "
            "Computes per-language-stratum metrics (id/en/mixed), "
            "per-label precision/recall/F1, and confusion counts. Runs "
            "entirely on stdlib; does not call or download any model."),
        epilog=(
            "Candidate model IDs (reference only - write their outputs to "
            "a predictions JSONL, this script does not run them):\n"
            "  sentiment (multilingual):  cardiffnlp/twitter-xlm-roberta-base-sentiment\n"
            "  sentiment (routed by lang): id -> w11wo/indonesian-roberta-base-sentiment-classifier\n"
            "                              en -> cardiffnlp/twitter-roberta-base-sentiment-latest\n"
            "  emotion (multilingual):    MilaNLProc/xlm-emo-t\n"
            "  emotion (diagnostic only):  id -> StevenLimcorn/indonesian-roberta-base-emotion-classifier\n"
            "                              en -> j-hartmann/emotion-english-distilroberta-base\n"
            "\n"
            "Labelled/predictions JSONL schema, one object per line:\n"
            "  id         string, unique\n"
            "  text       string, the comment text\n"
            "  language   one of: id, en, mixed\n"
            "  sentiment  string label\n"
            "  emotion    string label"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", help="Path to labelled JSONL (ground truth).")
    parser.add_argument("--predictions",
                         help="Path to predictions JSONL (same schema, model output). "
                              "Required unless --self-check.")
    parser.add_argument("--self-check", action="store_true",
                         help="Run the synthetic-data metric self-check and exit. "
                              "No files needed, no model calls.")
    args = parser.parse_args()

    if args.self_check:
        _self_check()
        return

    if not args.labels or not args.predictions:
        parser.error("--labels and --predictions are required unless --self-check")

    labels = load_jsonl(args.labels)
    predictions = load_jsonl(args.predictions)
    result = evaluate(labels, predictions)
    print_report(result)


if __name__ == "__main__":
    main()
