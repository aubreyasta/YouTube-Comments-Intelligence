"""
Get the comments and clean them.

Two API-efficiency notes, since this is where the wall-clock time goes:

  - Video metadata is one batched call for all videos, not one each.
    videos.list accepts up to 50 comma-separated IDs.
  - Comments and transcripts are fetched concurrently. Both are I/O
    bound, so a small thread pool cuts a six-video run from minutes to
    seconds. The pool is deliberately small: YouTube throttles bursts.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from langdetect import detect, DetectorFactory

import config

DetectorFactory.seed = 0        # makes language detection repeatable
WORKERS = 4

# Generic spam. Patterns, not topic words, so they hold whatever the
# video is about.
SPAM = re.compile(
    r"http\S+|\bgiveaway\b|\bfollback\b|follow ?back|subscribe ?back|"
    r"sub ?for ?sub|cek profil|check my|\bikutan\b|telegram\.me|"
    r"whats ?app|\bwa\.me\b", re.IGNORECASE)

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+")

QUESTION = re.compile(
    r"^\W*(?:berapa|kapan|apakah|gimana|bagaimana|kenapa|how|what|when|"
    r"why|which|is it|does it|can it)\b", re.IGNORECASE)

PRICE = re.compile(r"harga|mahal|murah|juta|\brp\b|price|expensive|cheap|"
                   r"worth|\$", re.IGNORECASE)

COMPETITOR = re.compile(r"\bvs\b|dibanding|banding|mending|meding|"
                        r"lebih baik|better than|compared to|rival|"
                        r"kompetitor", re.IGNORECASE)


def video_id(url):
    """Pull the 11-character ID out of any YouTube URL shape."""
    match = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})",
                      url)
    if not match:
        raise ValueError(f"No video ID found in: {url}")
    return match.group(1)


# ------------------------------------------------------------------ fetch

def _comments(youtube, vid, group, kind):
    rows, token = [], None
    while len(rows) < config.MAX_COMMENTS_PER_VIDEO:
        try:
            response = youtube.commentThreads().list(
                part="snippet,replies", videoId=vid, maxResults=100,
                pageToken=token, textFormat="plainText",
                order="relevance").execute()
        except HttpError as error:
            print(f"    ! {vid}: comments unavailable ({error.resp.status})")
            break

        for item in response["items"]:
            threads = [item["snippet"]["topLevelComment"]]
            threads += item.get("replies", {}).get("comments", [])
            for i, entry in enumerate(threads):
                snippet = entry["snippet"]
                rows.append({
                    "group": group, "kind": kind, "video_id": vid,
                    "comment": snippet["textDisplay"],
                    "likes": snippet["likeCount"],
                    "published_at": snippet["publishedAt"],
                    "is_reply": i > 0})

        token = response.get("nextPageToken")
        if not token:
            break
        time.sleep(0.15)
    return rows


def _transcript(vid):
    """Captions YouTube already publishes. Empty string if there are none."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        chunks = YouTubeTranscriptApi.get_transcript(vid,
                                                     languages=["id", "en"])
        return " ".join(c["text"] for c in chunks)[:20000]
    except Exception:
        return ""


def fetch():
    """Return (comments_df, meta_df)."""
    youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)
    entries = [(video_id(v["url"]), v.get("group", "Ungrouped"),
                v.get("kind", "auto")) for v in config.VIDEOS]

    # One batched metadata call rather than one per video.
    ids = [e[0] for e in entries]
    details = {}
    for i in range(0, len(ids), 50):
        response = youtube.videos().list(
            part="snippet,statistics", id=",".join(ids[i:i + 50])).execute()
        for item in response["items"]:
            details[item["id"]] = item

    # Comments and transcripts in parallel: both are I/O bound.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        comment_jobs = {vid: pool.submit(_comments, youtube, vid, g, k)
                        for vid, g, k in entries}
        transcript_jobs = {vid: pool.submit(_transcript, vid)
                           for vid, _, _ in entries}

    rows, meta = [], []
    for vid, group, kind in entries:
        got = comment_jobs[vid].result()
        transcript = transcript_jobs[vid].result()
        rows.extend(got)

        item = details.get(vid, {})
        snippet = item.get("snippet", {})
        meta.append({
            "video_id": vid, "group": group, "kind": kind,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "description": snippet.get("description", "")[:3000],
            "views": item.get("statistics", {}).get("viewCount", ""),
            "transcript": transcript,
            "has_transcript": bool(transcript)})
        print(f"    {group} / {vid}: {len(got)} comments, "
              f"transcript {'yes' if transcript else 'no'}")

    return pd.DataFrame(rows), pd.DataFrame(meta)


# ------------------------------------------------------------------ clean

def _language(text):
    stripped = re.sub(r"[^\w\s]", "", text).strip()
    if len(stripped) < config.MIN_COMMENT_LETTERS:
        return "too_short"
    try:
        return detect(stripped)
    except Exception:
        return "unknown"


def clean(df):
    """
    Drop junk, then attach the cheap deterministic labels.

    Order matters: spam and length first, so language detection (the
    slowest step here, and row-wise by nature) runs on as few rows as
    possible.
    """
    before = len(df)
    df = df.copy()
    df["comment"] = df["comment"].astype(str).str.strip()

    df = df[~df["comment"].str.contains(SPAM, na=False)]
    letters = df["comment"].str.replace(r"[^\w\s]", "", regex=True).str.strip()
    df = df[letters.str.len() >= config.MIN_COMMENT_LETTERS]
    df = df.drop_duplicates(subset=["comment"]).reset_index(drop=True)
    print(f"    cleaning: {len(df)} of {before} kept")

    # Vectorised flags. Topic-neutral by design, so they work on any video.
    df["n_words"] = df["comment"].str.split().str.len()
    df["is_question"] = (df["comment"].str.contains("?", regex=False)
                         | df["comment"].str.contains(QUESTION, na=False))
    df["has_emoji"] = df["comment"].str.contains(EMOJI, na=False)
    df["mentions_price"] = df["comment"].str.contains(PRICE, na=False)
    df["mentions_competitor"] = df["comment"].str.contains(COMPETITOR,
                                                           na=False)

    df["lang"] = df["comment"].map(_language)
    df["in_base"] = (df["lang"].isin(config.KEEP_LANGUAGES)
                     if config.KEEP_LANGUAGES else True)

    for group, sub in df.groupby("group"):
        print(f"    {group}: {int(sub['in_base'].sum())} of {len(sub)} "
              f"in target languages")
    return df