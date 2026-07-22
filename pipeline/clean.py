"""
Stage 2 - clean, filter, and attach cheap deterministic labels.

Nothing here calls an AI model. The point is to throw away junk and
work out everything a regular expression can work out, so that the
model later only has to do the part that actually needs dynamic 
judgement.
"""

import re
import pandas as pd
from langdetect import detect, DetectorFactory

import config as config

DetectorFactory.seed = 0     # makes language detection repeatable

# Generic spam. These are patterns, not topic words, so they work
# whatever the video is about.
SPAM = [
    r"http\S+", r"\bgiveaway\b", r"\bfollback\b", r"follow ?back",
    r"subscribe ?back", r"sub ?for ?sub", r"cek profil", r"check my",
    r"\bikutan\b", r"\bpromo code\b", r"\bbet\b", r"telegram\.me",
    r"whats ?app", r"\bwa\.me\b",
]

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+")


def letters_only(text):
    return re.sub(r"[^\w\s]", "", str(text)).strip()


def detect_language(text):
    stripped = letters_only(text)
    if len(stripped) < config.MIN_COMMENT_LETTERS:
        return "too_short"
    try:
        return detect(stripped)
    except Exception:
        return "unknown"


def run(df):
    before = len(df)
    df = df.copy()
    df["comment"] = df["comment"].astype(str).str.strip()

    # --- drop spam and near-empty comments
    df = df[~df["comment"].str.contains("|".join(SPAM), case=False,
                                        regex=True, na=False)]
    df = df[df["comment"].apply(
        lambda t: len(letters_only(t)) >= config.MIN_COMMENT_LETTERS)]
    df = df.drop_duplicates(subset=["comment"])
    print(f"    cleaning: {len(df)} of {before} kept")

    # --- cheap labels, all deterministic
    df["lang"] = df["comment"].apply(detect_language)
    df["n_words"] = df["comment"].str.split().str.len()
    # A question mark, or a question word in the first few words.
    # Matching question words anywhere gives false positives:
    # "too expensive for what it offers" is not a question.
    df["is_question"] = (
        df["comment"].str.contains(r"\?", regex=True, na=False)
        | df["comment"].str.contains(
            r"^\W*(?:berapa|kapan|apakah|gimana|bagaimana|kenapa|"
            r"how|what|when|why|which|is it|does it|can it)\b",
            case=False, regex=True, na=False))
    df["has_emoji"] = df["comment"].apply(lambda t: bool(EMOJI.search(t)))
    df["mentions_price"] = df["comment"].str.contains(
        r"harga|mahal|murah|juta|\brp\b|price|expensive|cheap|worth|\$",
        case=False, regex=True, na=False)
    df["mentions_competitor"] = df["comment"].str.contains(
        r"\bvs\b|dibanding|banding|mending|meding|lebih baik|better than|"
        r"compared to|rival|kompetitor", case=False, regex=True, na=False)

    # --- language filter
    if config.KEEP_LANGUAGES:
        df["in_base"] = df["lang"].isin(config.KEEP_LANGUAGES)
    else:
        df["in_base"] = True

    for group, sub in df.groupby("group"):
        kept = int(sub["in_base"].sum())
        print(f"    {group}: {kept} of {len(sub)} in target languages")

    return df.reset_index(drop=True)