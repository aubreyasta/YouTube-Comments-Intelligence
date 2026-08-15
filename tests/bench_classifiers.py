"""
Evaluation harness for the Indonesian-English Sentiment and Emotion
classifier candidates (see PRD.md, Phase 1 item 1: multilingual
scope is Indonesian, English, and mixed Indonesian-English comments;
Macro F1 is the selection metric, accuracy is secondary).

This script does not call or download any model. It scores one or more
predictions JSONL files against a labelled JSONL. Model inference is a
separate, later step; run today's benchmark by writing a candidate's
outputs to a predictions file and pointing the matching --*-predictions
flag at it.

Candidate model IDs (reference only, not downloaded or executed here):
  sentiment (single, multilingual): cardiffnlp/twitter-xlm-roberta-base-sentiment
  sentiment (routed by detected language):
    id -> w11wo/indonesian-roberta-base-sentiment-classifier
    en -> cardiffnlp/twitter-roberta-base-sentiment-latest
  emotion (multilingual): MilaNLProc/xlm-emo-t
  emotion (diagnostic only, language-specific):
    id -> StevenLimcorn/indonesian-roberta-base-emotion-classifier
    en -> j-hartmann/emotion-english-distilroberta-base

Labels file schema (JSONL, one object per line, ground truth):
  id         string, unique
  text       string, the comment text
  language   one of "id", "en", "mixed"
  sentiment  one of "negative", "neutral", "positive"
  emotion    one of "anger", "fear", "joy", "sadness", "other_neutral"

"other_neutral" is evaluation-only: it is reported for prevalence and
coverage but excluded from the four-label Emotion Macro F1 (see
shipping_macro_f1). It is not a shipping label.

Predictions file schema (JSONL, one object per line, model output). Two
variants, one field per file:
  sentiment predictions: {"id": str, "sentiment": str, "confidence": float, "model": str}
  emotion predictions:   {"id": str, "emotion": str, "confidence": float, "model": str}

"confidence" is accepted in predictions rows and ignored: it is never
read, validated, stored, or returned by any loader in this script.

Run:
  python tests/bench_classifiers.py --labels LABELS.jsonl --validate-only
  python tests/bench_classifiers.py --labels LABELS.jsonl \
      --sentiment-predictions SENT.jsonl --emotion-predictions EMO.jsonl \
      --qwen-sentiment-predictions QWEN_SENT.jsonl \
      --qwen-emotion-predictions QWEN_EMO.jsonl \
      --hardware "RTX 4080 16GB" --output results.json
  python tests/bench_classifiers.py --self-check
"""

import argparse
import datetime
import json
import sys
import tempfile
from collections import Counter, defaultdict

LABEL_REQUIRED_FIELDS = ("id", "text", "language", "sentiment", "emotion")
PREDICTION_REQUIRED_FIELDS = ("id", "model")
VALID_LANGUAGES = {"id", "en", "mixed"}
VALID_SENTIMENTS = {"negative", "neutral", "positive"}
VALID_EMOTIONS = {"anger", "fear", "joy", "sadness", "other_neutral"}
# other_neutral is evaluation-only: reported for prevalence and coverage,
# excluded from the four-label Emotion Macro F1. It is not a shipping label.
SHIPPING_EMOTIONS = {"anger", "fear", "joy", "sadness"}


# --------------------------------------------------------------- loading

def load_labels(path):
    """Read a labelled JSONL file, validating the label schema and enums."""
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
            missing = [field for field in LABEL_REQUIRED_FIELDS if field not in row]
            if missing:
                raise ValueError(f"{path}:{line_num}: missing fields {missing}")
            if not isinstance(row["id"], str) or not row["id"]:
                raise ValueError(f"{path}:{line_num}: id must be a non-empty string")
            if not isinstance(row["text"], str):
                raise ValueError(f"{path}:{line_num}: text must be a string")
            if row["language"] not in VALID_LANGUAGES:
                raise ValueError(
                    f"{path}:{line_num}: language must be one of "
                    f"{sorted(VALID_LANGUAGES)}, got {row['language']!r}")
            if row["sentiment"] not in VALID_SENTIMENTS:
                raise ValueError(
                    f"{path}:{line_num}: sentiment must be one of "
                    f"{sorted(VALID_SENTIMENTS)}, got {row['sentiment']!r}")
            if row["emotion"] not in VALID_EMOTIONS:
                raise ValueError(
                    f"{path}:{line_num}: emotion must be one of "
                    f"{sorted(VALID_EMOTIONS)}, got {row['emotion']!r}")
            if row["id"] in records:
                raise ValueError(f"{path}:{line_num}: duplicate id {row['id']!r}")
            records[row["id"]] = row
    if not records:
        raise ValueError(f"{path}: no rows")
    return records


def load_predictions(path, field):
    """
    Read a predictions JSONL file for one label field ("sentiment" or
    "emotion"), validating shape only. The predicted label is not checked
    against any enum: an out-of-vocabulary label is a scoring miss, not a
    load error. "confidence" is never read or stored.
    """
    required = tuple(PREDICTION_REQUIRED_FIELDS) + (field,)
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
            missing = [f for f in required if f not in row]
            if missing:
                raise ValueError(f"{path}:{line_num}: missing fields {missing}")
            if not isinstance(row["id"], str) or not row["id"]:
                raise ValueError(f"{path}:{line_num}: id must be a non-empty string")
            if not isinstance(row[field], str):
                raise ValueError(f"{path}:{line_num}: {field} must be a string")
            if row["id"] in records:
                raise ValueError(f"{path}:{line_num}: duplicate id {row['id']!r}")
            records[row["id"]] = {"id": row["id"], field: row[field], "model": row["model"]}
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


def shipping_macro_f1(y_true, y_pred, shipping_labels):
    """Macro F1 over shipping labels only. Rows whose true label is outside
    shipping_labels are dropped from the pair list first, so an
    evaluation-only label neither contributes an F1 term nor distorts the
    remaining ones."""
    pairs = [(t, p) for t, p in zip(y_true, y_pred) if t in shipping_labels]
    filtered_true = [t for t, _ in pairs]
    filtered_pred = [p for _, p in pairs]
    scores = per_label_scores(filtered_true, filtered_pred)
    total = 0.0
    for label in sorted(shipping_labels):
        total += scores.get(label, {}).get("f1", 0.0)
    return total / len(shipping_labels) if shipping_labels else 0.0


def coverage_report(y_true, y_pred, shipping_labels):
    """Counts of rows inside and outside the shipping label set, plus the
    per-label prevalence of every true label including evaluation-only ones."""
    prevalence = dict(Counter(y_true))
    shipping_n = sum(1 for t in y_true if t in shipping_labels)
    evaluation_only_n = sum(1 for t in y_true if t not in shipping_labels)
    return {
        "n": len(y_true),
        "shippingN": shipping_n,
        "evaluationOnlyN": evaluation_only_n,
        "prevalence": prevalence,
    }


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


# ------------------------------------------------------------------ JSON output

def confusion_for_json(counts):
    """confusion_counts() keys are (true, pred) tuples, which json.dumps
    cannot encode as object keys. Emit a list of explicit triples."""
    return [{"true": t, "predicted": p, "count": c}
            for (t, p), c in sorted(counts.items())]


def build_record(hardware, systems, decision):
    """Assemble the top-level JSON output record. `systems` is a list of
    fully-formed system entries (see module docstring / packet for the
    fourteen-key shape); `decision` is the decision dict."""
    return {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "hardware": hardware,
        "systems": systems,
        "decision": decision,
    }


def _not_run_system(task, model):
    return {
        "task": task,
        "model": model,
        "revision": None,
        "license": None,
        "downloadBytes": None,
        "runtimeSeconds": None,
        "gpuMemoryMiB": None,
        "overallMacroF1": None,
        "accuracy": None,
        "strata": {},
        "labels": {},
        "confusion": [],
        "coverage": None,
        "status": "not run",
    }


def prediction_model_id(predictions, path):
    """The single model identifier every row in a prediction file must agree on.

    The identifier is data, not a caller-supplied label: a later task pins
    exact revisions from this record, so a placeholder would be worse than
    an error. A file mixing two models is a data fault, not something to
    silently pick a winner from.
    """
    values = sorted({row["model"] for row in predictions.values()})
    if len(values) > 1:
        raise ValueError(f"{path}: prediction rows disagree about model: {values}")
    return values[0]


def _scored_system(task, model_id, labels, predictions):
    ids = sorted(set(labels) & set(predictions))
    y_true = [labels[i][task] for i in ids]
    y_pred = [predictions[i][task] for i in ids]

    if task == "emotion":
        overall = shipping_macro_f1(y_true, y_pred, SHIPPING_EMOTIONS)
        coverage = coverage_report(y_true, y_pred, SHIPPING_EMOTIONS)
    else:
        overall = macro_f1(y_true, y_pred)
        coverage = None

    strata = {}
    by_lang = defaultdict(list)
    for i in ids:
        by_lang[labels[i]["language"]].append(i)
    for lang in ("id", "en", "mixed"):
        lang_ids = by_lang.get(lang, [])
        lt = [labels[i][task] for i in lang_ids]
        lp = [predictions[i][task] for i in lang_ids]
        strata[lang] = {
            "n": len(lang_ids),
            "macroF1": macro_f1(lt, lp),
            "accuracy": accuracy(lt, lp),
        }

    return {
        "task": task,
        "model": model_id,
        "revision": None,
        "license": None,
        "downloadBytes": None,
        "runtimeSeconds": None,
        "gpuMemoryMiB": None,
        "overallMacroF1": overall,
        "accuracy": accuracy(y_true, y_pred),
        "strata": strata,
        "labels": per_label_scores(y_true, y_pred),
        "confusion": confusion_for_json(confusion_counts(y_true, y_pred)),
        "coverage": coverage,
        "status": "scored",
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

    # ---- new coverage below: loaders, enum validation, build_record ----

    with tempfile.TemporaryDirectory() as tmp:
        import os

        # A valid labels file with three rows, one per language, loads and
        # returns three records.
        valid_labels_path = os.path.join(tmp, "valid_labels.jsonl")
        with open(valid_labels_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "a1", "text": "bagus", "language": "id",
                                 "sentiment": "positive", "emotion": "joy"}) + "\n")
            f.write(json.dumps({"id": "a2", "text": "great", "language": "en",
                                 "sentiment": "positive", "emotion": "joy"}) + "\n")
            f.write(json.dumps({"id": "a3", "text": "not bad lah", "language": "mixed",
                                 "sentiment": "neutral", "emotion": "other_neutral"}) + "\n")
        loaded_labels = load_labels(valid_labels_path)
        assert len(loaded_labels) == 3

        # A labels row with "sentiment": "furious" raises ValueError whose
        # message contains the file path, ":2:", and "sentiment".
        bad_sentiment_path = os.path.join(tmp, "bad_sentiment.jsonl")
        with open(bad_sentiment_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "b1", "text": "ok", "language": "id",
                                 "sentiment": "positive", "emotion": "joy"}) + "\n")
            f.write(json.dumps({"id": "b2", "text": "ok", "language": "id",
                                 "sentiment": "furious", "emotion": "joy"}) + "\n")
        try:
            load_labels(bad_sentiment_path)
            raise AssertionError("expected ValueError for invalid sentiment")
        except ValueError as exc:
            msg = str(exc)
            assert bad_sentiment_path in msg
            assert ":2:" in msg
            assert "sentiment" in msg

        # A labels row with "emotion": "ecstatic" raises ValueError
        # mentioning "emotion".
        bad_emotion_path = os.path.join(tmp, "bad_emotion.jsonl")
        with open(bad_emotion_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "c1", "text": "ok", "language": "id",
                                 "sentiment": "positive", "emotion": "ecstatic"}) + "\n")
        try:
            load_labels(bad_emotion_path)
            raise AssertionError("expected ValueError for invalid emotion")
        except ValueError as exc:
            assert "emotion" in str(exc)

        # A labels row with "id": "" raises ValueError.
        bad_id_path = os.path.join(tmp, "bad_id.jsonl")
        with open(bad_id_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "", "text": "ok", "language": "id",
                                 "sentiment": "positive", "emotion": "joy"}) + "\n")
        try:
            load_labels(bad_id_path)
            raise AssertionError("expected ValueError for empty id")
        except ValueError:
            pass

        # A PRD-shaped sentiment prediction file loads cleanly through
        # load_predictions(path, "sentiment") and the returned records
        # contain no "confidence" key.
        sentiment_preds_path = os.path.join(tmp, "sentiment_preds.jsonl")
        with open(sentiment_preds_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "c1", "sentiment": "positive",
                                 "confidence": 1.0, "model": "qwen3:14b-q4_K_M"}) + "\n")
        sentiment_preds = load_predictions(sentiment_preds_path, "sentiment")
        assert "confidence" not in sentiment_preds["c1"]

        # The same file loaded with field="emotion" raises ValueError for
        # the missing "emotion" field.
        try:
            load_predictions(sentiment_preds_path, "emotion")
            raise AssertionError("expected ValueError for missing emotion field")
        except ValueError as exc:
            assert "emotion" in str(exc)

        # A prediction row with an out-of-vocabulary label loads without
        # raising.
        oov_preds_path = os.path.join(tmp, "oov_preds.jsonl")
        with open(oov_preds_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "d1", "sentiment": "sarcastic",
                                 "confidence": 0.5, "model": "some-model"}) + "\n")
        oov_preds = load_predictions(oov_preds_path, "sentiment")
        assert oov_preds["d1"]["sentiment"] == "sarcastic"

        # No loader return value contains a "confidence" key, for both a
        # sentiment and an emotion prediction fixture.
        emotion_preds_path = os.path.join(tmp, "emotion_preds.jsonl")
        with open(emotion_preds_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "e1", "emotion": "anger",
                                 "confidence": 0.8, "model": "some-model"}) + "\n")
        emotion_preds = load_predictions(emotion_preds_path, "emotion")
        for rec in sentiment_preds.values():
            assert "confidence" not in rec
        for rec in emotion_preds.values():
            assert "confidence" not in rec

        # prediction_model_id: a file whose rows all agree on "model"
        # yields that single identifier.
        agree_preds_path = os.path.join(tmp, "agree_preds.jsonl")
        with open(agree_preds_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "f1", "sentiment": "positive",
                                 "confidence": 0.9,
                                 "model": "cardiffnlp/twitter-xlm-roberta-base-sentiment"}) + "\n")
            f.write(json.dumps({"id": "f2", "sentiment": "negative",
                                 "confidence": 0.9,
                                 "model": "cardiffnlp/twitter-xlm-roberta-base-sentiment"}) + "\n")
            f.write(json.dumps({"id": "f3", "sentiment": "neutral",
                                 "confidence": 0.9,
                                 "model": "cardiffnlp/twitter-xlm-roberta-base-sentiment"}) + "\n")
        agree_preds = load_predictions(agree_preds_path, "sentiment")
        assert (prediction_model_id(agree_preds, agree_preds_path)
                == "cardiffnlp/twitter-xlm-roberta-base-sentiment")

        # A file whose rows disagree about "model" raises ValueError naming
        # the file and both conflicting values.
        disagree_preds_path = os.path.join(tmp, "disagree_preds.jsonl")
        with open(disagree_preds_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "g1", "sentiment": "positive",
                                 "confidence": 0.9, "model": "model-a"}) + "\n")
            f.write(json.dumps({"id": "g2", "sentiment": "negative",
                                 "confidence": 0.9, "model": "model-b"}) + "\n")
        disagree_preds = load_predictions(disagree_preds_path, "sentiment")
        try:
            prediction_model_id(disagree_preds, disagree_preds_path)
            raise AssertionError("expected ValueError for disagreeing model values")
        except ValueError as exc:
            msg = str(exc)
            assert disagree_preds_path in msg
            assert "model-a" in msg
            assert "model-b" in msg

    # shipping_macro_f1 excludes other_neutral; must differ from plain
    # macro_f1 on the same pairs. Fixture: 4 shipping rows all correct
    # (f1=1.0 each -> shipping_macro_f1 == 1.0), plus 2 other_neutral rows
    # both misclassified as "anger" (adds an "other_neutral" vs "anger"
    # confusion that drags plain macro_f1 down via anger's precision).
    cov_y_true = ["anger", "fear", "joy", "sadness", "other_neutral", "other_neutral"]
    cov_y_pred = ["anger", "fear", "joy", "sadness", "anger", "anger"]
    shipping_f1 = shipping_macro_f1(cov_y_true, cov_y_pred, SHIPPING_EMOTIONS)
    plain_f1 = macro_f1(cov_y_true, cov_y_pred)
    assert shipping_f1 == 1.0, f"expected shipping_macro_f1 1.0, got {shipping_f1}"
    assert shipping_f1 != plain_f1, "shipping_macro_f1 must differ from plain macro_f1 here"

    # coverage_report reports the other_neutral count in evaluationOnlyN
    # and in prevalence.
    cov = coverage_report(cov_y_true, cov_y_pred, SHIPPING_EMOTIONS)
    assert cov["evaluationOnlyN"] == 2
    assert cov["prevalence"]["other_neutral"] == 2
    assert cov["shippingN"] == 4
    assert cov["n"] == 6

    # build_record output survives json.dumps/json.loads round trip, with
    # the exact top-level and system-entry key shapes.
    not_run_sentiment = _not_run_system("sentiment", "qwen-sentiment-placeholder")
    not_run_emotion = _not_run_system("emotion", "qwen-emotion-placeholder")
    record = build_record(
        hardware="unrecorded",
        systems=[not_run_sentiment, not_run_emotion],
        decision={
            "sentimentProducer": None,
            "emotionProducer": None,
            "sentimentConfidenceColumnSurvives": False,
            "emotionConfidenceColumnSurvives": False,
            "notes": "selection is not automated in this task",
        },
    )
    dumped = json.dumps(record)
    round_tripped = json.loads(dumped)
    assert round_tripped == record

    assert list(record.keys()) == ["generatedAt", "hardware", "systems", "decision"]
    expected_system_keys = [
        "task", "model", "revision", "license", "downloadBytes",
        "runtimeSeconds", "gpuMemoryMiB", "overallMacroF1", "accuracy",
        "strata", "labels", "confusion", "coverage", "status",
    ]
    assert list(record["systems"][0].keys()) == expected_system_keys

    # _scored_system reports the real identifier in "model" and keeps the
    # fourteen-key shape and order.
    scored = _scored_system("sentiment", "qwen3:14b-q4_K_M", labels, predictions)
    assert scored["model"] == "qwen3:14b-q4_K_M"
    assert list(scored.keys()) == expected_system_keys

    # _not_run_system still reports the slot label it was given.
    assert not_run_sentiment["model"] == "qwen-sentiment-placeholder"
    assert not_run_emotion["model"] == "qwen-emotion-placeholder"

    # A record built with both Qwen systems absent has those systems
    # marked status == "not run" with overallMacroF1 is None.
    for system in record["systems"]:
        assert system["status"] == "not run"
        assert system["overallMacroF1"] is None

    print("self-check: PASS (macro F1 exposed majority-only prediction; "
          f"accuracy={acc:.3f} vs macro_f1={mf1:.3f}; "
          f"shipping_macro_f1={shipping_f1:.3f} vs macro_f1={plain_f1:.3f})")


# ------------------------------------------------------------------ CLI

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Score Indonesian-English Sentiment and Emotion classifier "
            "predictions against labelled data. Macro F1 is primary "
            "(selection metric per PRD.md), accuracy is secondary. "
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
            "Labels JSONL schema, one object per line (ground truth):\n"
            "  id         string, unique\n"
            "  text       string, the comment text\n"
            "  language   one of: id, en, mixed\n"
            "  sentiment  one of: negative, neutral, positive\n"
            "  emotion    one of: anger, fear, joy, sadness, other_neutral\n"
            "             (other_neutral is evaluation-only; excluded from\n"
            "             the four-label Emotion Macro F1)\n"
            "\n"
            "Predictions JSONL schema, one object per line, one field per file:\n"
            "  sentiment predictions: id, sentiment, confidence, model\n"
            "  emotion predictions:   id, emotion, confidence, model\n"
            "  (confidence is accepted and ignored; never read or stored)"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", help="Path to labelled JSONL (ground truth).")
    parser.add_argument("--validate-only", action="store_true",
                         help="Validate --labels, print counts, and exit. "
                              "No model work, no predictions file required.")
    parser.add_argument("--sentiment-predictions",
                         help="Path to sentiment predictions JSONL (encoder candidate).")
    parser.add_argument("--emotion-predictions",
                         help="Path to emotion predictions JSONL (encoder candidate).")
    parser.add_argument("--qwen-sentiment-predictions",
                         help="Path to sentiment predictions JSONL from the Qwen candidate. Optional.")
    parser.add_argument("--qwen-emotion-predictions",
                         help="Path to emotion predictions JSONL from the Qwen candidate. Optional.")
    parser.add_argument("--hardware", default="unrecorded",
                         help="Free-text hardware description recorded in the output JSON.")
    parser.add_argument("--output", help="Path to write the JSON result record.")
    parser.add_argument("--self-check", action="store_true",
                         help="Run the synthetic-data metric self-check and exit. "
                              "No files needed, no model calls.")
    args = parser.parse_args()

    if args.self_check:
        _self_check()
        return

    if args.validate_only:
        if not args.labels:
            parser.error("--validate-only requires --labels")
        labels = load_labels(args.labels)
        print(f"total rows: {len(labels)}")
        by_language = Counter(row["language"] for row in labels.values())
        print("by language:")
        for lang, count in sorted(by_language.items()):
            print(f"  {lang:8s} {count}")
        by_sentiment = Counter(row["sentiment"] for row in labels.values())
        print("by sentiment:")
        for label, count in sorted(by_sentiment.items()):
            print(f"  {label:10s} {count}")
        by_emotion = Counter(row["emotion"] for row in labels.values())
        print("by emotion:")
        for label, count in sorted(by_emotion.items()):
            print(f"  {label:14s} {count}")
        return

    if not args.labels:
        parser.error("--labels is required unless --self-check or --validate-only")

    labels = load_labels(args.labels)

    system_specs = [
        ("sentiment", args.sentiment_predictions, "encoder-sentiment-candidate"),
        ("emotion", args.emotion_predictions, "encoder-emotion-candidate"),
        ("sentiment", args.qwen_sentiment_predictions, "qwen-sentiment-candidate"),
        ("emotion", args.qwen_emotion_predictions, "qwen-emotion-candidate"),
    ]

    systems = []
    for task, path, slot_label in system_specs:
        if not path:
            systems.append(_not_run_system(task, slot_label))
            continue
        predictions = load_predictions(path, task)
        model_id = prediction_model_id(predictions, path)
        system = _scored_system(task, model_id, labels, predictions)
        systems.append(system)
        report = evaluate_field(labels, predictions, task)
        print(f"\n=== {model_id} ({task}) ===")
        print_field_report(task, report)

    decision = {
        "sentimentProducer": None,
        "emotionProducer": None,
        "sentimentConfidenceColumnSurvives": False,
        "emotionConfidenceColumnSurvives": False,
        "notes": ("Winner selection is not automated in this task; scoring "
                  "inputs alone do not make a winner unambiguous."),
    }
    record = build_record(hardware=args.hardware, systems=systems, decision=decision)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
        print(f"\nwrote result record to {args.output}")


if __name__ == "__main__":
    main()
