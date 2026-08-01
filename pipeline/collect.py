"""
Get the comments and clean them.

Two API-efficiency notes, since this is where the wall-clock time goes:

  - Video metadata is one batched call for all videos, not one each.
    videos.list accepts up to 50 comma-separated IDs.
  - Comments and transcripts are fetched concurrently. Both are I/O
    bound, so a small thread pool cuts a six-video run from minutes to
    seconds. The pool is deliberately small: YouTube throttles bursts.

IMPORTANT: googleapiclient sits on httplib2, which is NOT thread-safe. A
service object shared across threads has them reading from one socket,
which surfaces as SSL record-layer failures or "NoneType has no attribute
read" from deep inside http.client. Each thread therefore builds its own
service through _service(), and none is ever passed between threads.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from langdetect import detect, DetectorFactory

from pipeline.config_types import PipelineConfig

DetectorFactory.seed = 0        # makes language detection repeatable
WORKERS = 4
NETWORK_RETRIES = 3

_local = threading.local()


def _service(api_key: str):
    """
    A YouTube client belonging to the calling thread.

    Never share one of these. See the module docstring: httplib2 is not
    thread-safe, and sharing produces corrupted reads rather than a clean
    error. cache_discovery=False avoids a second shared-state problem,
    the on-disk discovery cache.
    """
    if not hasattr(_local, "youtube"):
        _local.youtube = build("youtube", "v3",
                               developerKey=api_key,
                               cache_discovery=False)
    return _local.youtube

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

def _page(vid, token, api_key: str):
    """
    One page of comments, retried on transient network trouble.

    HttpError is not retried here: a 403 means comments are disabled and a
    404 means the video is gone, and neither improves on a second attempt.
    Socket and SSL errors do improve, so those get a backoff.
    """
    for attempt in range(NETWORK_RETRIES):
        try:
            return _service(api_key).commentThreads().list(
                part="snippet,replies", videoId=vid, maxResults=100,
                pageToken=token, textFormat="plainText",
                order="relevance").execute()
        except HttpError:
            raise
        except Exception as error:
            if attempt == NETWORK_RETRIES - 1:
                raise
            wait = 2 ** attempt
            print(f"    . {vid}: {type(error).__name__}, retrying in {wait}s")
            # A broken connection stays broken, so drop this thread's
            # client and let the retry build a fresh one.
            if hasattr(_local, "youtube"):
                del _local.youtube
            time.sleep(wait)


def _comments(vid, group, kind, max_comments: int, api_key: str):
    rows, token = [], None
    while len(rows) < max_comments:
        try:
            response = _page(vid, token, api_key)
        except HttpError as error:
            print(f"    ! {vid}: comments unavailable ({error.resp.status})")
            break
        except Exception as error:
            # One video failing should not lose the other five.
            print(f"    ! {vid}: gave up after {NETWORK_RETRIES} attempts "
                  f"({type(error).__name__}). Continuing with "
                  f"{len(rows)} comments.")
            break

        for item in response["items"]:
            top_reply_count = item["snippet"].get("totalReplyCount", 0)
            threads = [item["snippet"]["topLevelComment"]]
            threads += item.get("replies", {}).get("comments", [])
            for i, entry in enumerate(threads):
                snippet = entry["snippet"]
                rows.append({
                    "group": group, "kind": kind, "video_id": vid,
                    "comment": snippet["textDisplay"],
                    "likes": snippet["likeCount"],
                    "published_at": snippet["publishedAt"],
                    "is_reply": i > 0,
                    "reply_count": 0 if i > 0 else top_reply_count})

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


def fetch(cfg: "PipelineConfig"):
    """Return (comments_df, meta_df)."""
    entries = [(video_id(v["url"]), v.get("group", "Ungrouped"),
                v.get("kind", "auto")) for v in cfg.VIDEOS]

    # One batched metadata call rather than one per video. Runs on the
    # main thread, so it gets that thread's own client.
    ids = [e[0] for e in entries]
    details = {}
    for i in range(0, len(ids), 50):
        response = _service(cfg.YOUTUBE_API_KEY).videos().list(
            part="snippet,statistics", id=",".join(ids[i:i + 50])).execute()
        for item in response["items"]:
            details[item["id"]] = item

    # Comments and transcripts in parallel: both are I/O bound. Each
    # worker builds its own client on first use.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        comment_jobs = {vid: pool.submit(
                            _comments, vid, g, k,
                            cfg.MAX_COMMENTS_PER_VIDEO, cfg.YOUTUBE_API_KEY)
                        for vid, g, k in entries}
        transcript_jobs = {vid: pool.submit(_transcript, vid)
                           for vid, _, _ in entries}

    rows, meta = [], []
    for vid, group, kind in entries:
        try:
            got = comment_jobs[vid].result()
        except Exception as error:
            print(f"    ! {vid}: {type(error).__name__}, skipped")
            got = []
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

    if not rows:
        raise RuntimeError(
            "No comments collected from any video. Check the URLs, and "
            "check that comments are enabled on them.")

    return pd.DataFrame(rows), pd.DataFrame(meta)


# ------------------------------------------------------------------ clean

def _language(text, min_letters: int):
    stripped = re.sub(r"[^\w\s]", "", text).strip()
    if len(stripped) < min_letters:
        return "too_short"
    try:
        return detect(stripped)
    except Exception:
        return "unknown"


def clean(df, cfg: "PipelineConfig"):
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
    df = df[letters.str.len() >= cfg.MIN_COMMENT_LETTERS]
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

    df["lang"] = df["comment"].map(
        lambda t: _language(t, cfg.MIN_COMMENT_LETTERS))
    df["in_base"] = (df["lang"].isin(cfg.KEEP_LANGUAGES)
                     if cfg.KEEP_LANGUAGES else True)

    for group, sub in df.groupby("group"):
        print(f"    {group}: {int(sub['in_base'].sum())} of {len(sub)} "
              f"in target languages")
    return df