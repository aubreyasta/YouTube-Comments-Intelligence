"""
Stage 1 - pull comments and the transcript for each video.

Comments come from the YouTube Data API.
The transcript comes from youtube-transcript-api, which reads the
captions YouTube already publishes. If a video has no captions we
fall back to its title and description, which is usually enough to
tell what the video was pushing.
"""

import re
import time
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config as config


def video_id_from_url(url):
    """Pull the 11-character ID out of any YouTube URL shape."""
    patterns = [
        r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not find a video ID in: {url}")


def fetch_comments(youtube, video_id, group, kind):
    rows, token = [], None

    while len(rows) < config.MAX_COMMENTS_PER_VIDEO:
        try:
            response = youtube.commentThreads().list(
                part="snippet,replies", videoId=video_id, maxResults=100,
                pageToken=token, textFormat="plainText", order="relevance",
            ).execute()
        except HttpError as error:
            print(f"    ! comments unavailable ({error.resp.status})")
            break

        for item in response["items"]:
            top = item["snippet"]["topLevelComment"]["snippet"]
            rows.append({
                "group": group, "kind": kind, "video_id": video_id,
                "comment": top["textDisplay"], "likes": top["likeCount"],
                "published_at": top["publishedAt"], "is_reply": False,
            })
            for reply in item.get("replies", {}).get("comments", []):
                snippet = reply["snippet"]
                rows.append({
                    "group": group, "kind": kind, "video_id": video_id,
                    "comment": snippet["textDisplay"],
                    "likes": snippet["likeCount"],
                    "published_at": snippet["publishedAt"], "is_reply": True,
                })

        token = response.get("nextPageToken")
        if not token:
            break
        time.sleep(0.2)

    return rows


def fetch_metadata(youtube, video_id):
    response = youtube.videos().list(part="snippet,statistics",
                                     id=video_id).execute()
    if not response["items"]:
        return {}
    snippet = response["items"][0]["snippet"]
    stats = response["items"][0].get("statistics", {})
    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "description": snippet.get("description", "")[:4000],
        "published_at": snippet.get("publishedAt", ""),
        "views": stats.get("viewCount", ""),
    }


def fetch_transcript(video_id):
    """Return the caption text, or an empty string if there are none."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        # Try Indonesian first, then English, then whatever exists.
        chunks = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["id", "en"])
        return " ".join(chunk["text"] for chunk in chunks)[:20000]
    except Exception:
        return ""


def run():
    youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)
    all_comments, all_meta = [], []

    for entry in config.VIDEOS:
        video_id = video_id_from_url(entry["url"])
        print(f"  {entry['group']} / {entry['kind']} / {video_id}")

        meta = fetch_metadata(youtube, video_id)
        meta["group"] = entry["group"]
        meta["kind"] = entry["kind"]
        meta["transcript"] = fetch_transcript(video_id)
        meta["has_transcript"] = bool(meta["transcript"])
        all_meta.append(meta)

        comments = fetch_comments(youtube, video_id, entry["group"],
                                  entry["kind"])
        print(f"    {len(comments)} comments, "
              f"transcript: {'yes' if meta['has_transcript'] else 'no'}")
        all_comments.extend(comments)

    return pd.DataFrame(all_comments), pd.DataFrame(all_meta)