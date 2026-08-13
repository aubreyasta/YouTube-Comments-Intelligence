"""
Work out what each campaign put forward.

One call per group, covering all of that group's videos, rather than one
call per video plus one per group. For six videos across three groups
that is 3 calls instead of 9, and each call still sees only one
campaign, so nothing is diluted.

The call is grounded only: every claim is read from the transcripts and
is checkable. No ungrounded background is generated or returned.

Two more entry points sit on top of that grounded call, for the
Session-level Key Messages the setup screen shows before any run:

- draft_from_inputs(): draft from User Inputs alone (documents, article
  text, uploaded images). No transcripts.
- reconcile(): reconcile an existing ordered Key Message list against
  transcripts at run time, preserving stable ids and manual edits.

Both return plain KeyMessage-shaped dicts (id, label, description,
included, order, edited); DB storage is the caller's job.
"""

import uuid

from pipeline import llm
from pipeline.config_types import PipelineConfig

LENS = {
    "brand_ad": "the CREATIVE DECISIONS the brand made: the narrative or "
                "hook it leads with, the positioning, the attributes it "
                "foregrounds, and any mechanic such as scarcity, a price "
                "claim or a co-branding partner",
    "review": "what the CREATOR foregrounded: which features they spent "
              "time on, what they praised, what they criticised, and the "
              "comparisons they drew",
    "explainer": "the CLAIMS AND TOPICS put forward: the main argument, "
                 "the sub-topics, the framing, and anything presented as "
                 "surprising or contentious",
    "auto": "the main things put forward: the hook or argument and the "
            "specific points foregrounded. First decide whether this is a "
            "brand ad, an independent review, or an explainer",
}

PROMPT = """Analyse one advertising campaign, or one set of related videos.

CAMPAIGN: {group}
CONTEXT SUPPLIED: {context}

For each video below, identify {lens}.

{videos}

Return JSON:
{{
  "summary": "one sentence on what this campaign is",
  "points": [
    {{
      "label": "short name for the idea, 3 to 6 words",
      "video_id": "which video above this came from",
      "description": "one sentence on what the video says about it"
    }}
  ]
}}

Give 4 to 8 points per video, ordered by how central they are."""


def _bounded_prompt(group, lens, context, blocks):
    """Keep the fixed section headers while trimming the largest sections."""
    context = str(context or "(none supplied)")
    videos = "\n\n".join(blocks)
    prompt = PROMPT.format(group=group, lens=lens, context=context,
                           videos=videos)
    if len(prompt) <= 80000:
        return prompt
    fixed = PROMPT.format(group=group, lens=lens, context="", videos="")
    available = max(0, 80000 - len(fixed))
    context_budget = min(len(context), available // 4)
    return PROMPT.format(group=group, lens=lens,
                         context=context[:context_budget],
                         videos=videos[:available - context_budget])


def run(meta_df, cfg: PipelineConfig, context_map=None,
        images_map=None):
    """Return (grounded_markdown, points)."""
    grounded, points = [], []
    context_map = dict(context_map or {})

    images_submitted = any(images for images in (images_map or {}).values())
    try:
        for group, images in (images_map or {}).items():
            if not images:
                continue
            observations = llm.extract_image_context(images, cfg)
            if isinstance(observations, (list, tuple)):
                observations = "\n".join(str(item) for item in observations)
            existing = context_map.get(group, "")
            context_map[group] = "\n\n".join(part for part in (
                existing, "IMAGE OBSERVATIONS:\n" + str(observations)
            ) if part)
    finally:
        # Best-effort: only unload the vision model if it was actually
        # loaded (i.e. at least one image was submitted), and never let
        # an unload failure mask the original extraction error.
        if images_submitted:
            try:
                llm.unload(cfg.VISION_MODEL, cfg)
            except Exception:
                pass

    for group, sub in meta_df.groupby("group", sort=False):
        kinds = [k for k in sub["kind"].unique() if k in LENS]
        lens = LENS[kinds[0]] if len(kinds) == 1 else LENS["auto"]

        blocks = []
        for _, row in sub.iterrows():
            transcript = row["transcript"] or "(no captions available)"
            blocks.append(
                f"--- VIDEO {row['video_id']} ({row['kind']})\n"
                f"TITLE: {row['title']}\n"
                f"CHANNEL: {row['channel']}\n"
                f"DESCRIPTION: {row['description'][:1200]}\n"
                f"TRANSCRIPT: {transcript[:10000]}")

        print(f"    briefing {group} ({len(sub)} video"
              f"{'s' if len(sub) > 1 else ''})")
        result = llm.ask_json(
            _bounded_prompt(group, lens,
                            context_map.get(group, "(none supplied)"), blocks),
            cfg, schema=llm.BRIEF_SCHEMA, validation=llm.validate_brief,
            num_predict=3072)

        known = set(sub["video_id"])
        for point in result.get("points", []):
            vid = point.get("video_id", "")
            points.append({
                "group": group,
                "video_id": vid if vid in known else sub.iloc[0]["video_id"],
                "label": point["label"],
                "description": point.get("description", "")})

        lines = [f"## {group}", "", result.get("summary", ""), "",
                 "**What the videos put forward:**", ""]
        for point in result.get("points", []):
            lines.append(f"- **{point['label']}** ({point.get('video_id','')})"
                         f" - {point.get('description', '')}")
        if not sub["has_transcript"].any():
            lines += ["", "> No captions on any video in this group, so this "
                          "brief rests on titles and descriptions. Weaker "
                          "evidence."]
        grounded.append("\n".join(lines))

    return ("\n\n".join(grounded), points)


# ---------------------------------------------------------------------------
# Session-level Key Messages: draft from User Inputs, reconcile at run time
# ---------------------------------------------------------------------------
#
# `run()` above produces (grounded_markdown, points) keyed by group/video_id,
# for the per-comment classification step. The two entry points below
# produce the separate KeyMessage shape the setup screen stores and edits:
# {id, label, description, included, order, edited}. Neither touches the
# DB; the caller (server.py, later) is responsible for storage.

INPUTS_PROMPT = """Read the material a campaign owner supplied about their \
own campaign (documents, article text, and any image observations below).

MATERIAL:
{material}

Identify the key messages this material puts forward: the claims, \
features, and positioning it wants an audience to take away. Use only \
what is written or shown above; do not add anything from general \
knowledge.

Return JSON:
{{
  "summary": "one sentence on what this material is",
  "points": [
    {{
      "label": "short name for the message, 3 to 6 words",
      "video_id": "material",
      "description": "one sentence on what the material says about it"
    }}
  ]
}}

Give 3 to 8 points, ordered by how central they are."""


def _key_message(label, description, *, included=True, order=0, edited=False,
                 id_factory=uuid.uuid4):
    return {
        "id": str(id_factory()),
        "label": label,
        "description": description,
        "included": included,
        "order": order,
        "edited": edited,
    }


def draft_from_inputs(text, images, cfg: PipelineConfig, *, id_factory=uuid.uuid4):
    """
    Draft Key Messages from User Inputs alone: document/article text plus
    uploaded images. No transcripts, no background or model-memory
    grounding.

    `text` is the combined document/article text (str, may be empty).
    `images` is a list of (bytes, mime_type) tuples, as llm.ask_json
    expects; may be empty.

    Returns a list of KeyMessage dicts. Empty input (no text, no images)
    returns [] without calling the model.
    """
    text = str(text or "").strip()
    images = list(images or [])
    if not text and not images:
        return []

    material_parts = [text] if text else []
    images_submitted = bool(images)
    try:
        if images:
            observations = llm.extract_image_context(images, cfg)
            material_parts.append("IMAGE OBSERVATIONS:\n" + str(observations))
    finally:
        if images_submitted:
            try:
                llm.unload(cfg.VISION_MODEL, cfg)
            except Exception:
                pass

    material = "\n\n".join(material_parts)
    result = llm.ask_json(
        INPUTS_PROMPT.format(material=material[:80000]),
        cfg, schema=llm.BRIEF_SCHEMA, validation=llm.validate_brief,
        num_predict=3072)

    return [
        _key_message(point["label"], point.get("description", ""),
                     order=order, id_factory=id_factory)
        for order, point in enumerate(result.get("points", []))
    ]


def reconcile(existing, meta_df, cfg: PipelineConfig, context_map=None,
              images_map=None, *, id_factory=uuid.uuid4, include_grounded=False):
    """
    Reconcile an existing ordered Key Message list against transcripts at
    run time.

    `existing` is the current ordered list of KeyMessage dicts (possibly
    empty, e.g. a Session with no User Inputs). Entries marked
    `edited=True` are kept exactly as given. Unedited existing entries
    that match a transcript-derived label (case-insensitively) keep their
    stable id but take the freshly-grounded description. Transcript-only
    labels are appended as new entries via `id_factory`. Existing entries
    with no transcript match are kept as-is (nothing here re-grounds or
    drops a message the model didn't happen to re-derive).

    If `existing` is empty, the transcript-derived points become the
    initial list. If there is neither an existing list nor any
    transcript-derived points, returns [].

    Returns a list of KeyMessage dicts in stable order: existing entries
    first (in their given order), then new transcript-only additions.

    `include_grounded=True` returns `(grounded_markdown, reconciled)`
    instead - adapter.py needs that markdown for report.json and the
    report body, and reconcile() already makes the one `run()` call that
    produces it; a second call would double the model cost for the same
    transcripts. Defaults False so existing callers keep getting the
    plain list.
    """
    existing = list(existing or [])
    grounded, points = run(meta_df, cfg, context_map=context_map, images_map=images_map)

    derived = []
    seen_labels = set()
    for point in points:
        label = point["label"]
        key = label.strip().lower()
        if key in seen_labels:
            continue
        seen_labels.add(key)
        derived.append({"label": label, "description": point.get("description", "")})

    derived_by_key = {item["label"].strip().lower(): item for item in derived}
    matched_keys = set()

    reconciled = []
    for entry in existing:
        entry = dict(entry)
        if not entry.get("edited"):
            match = derived_by_key.get(str(entry.get("label", "")).strip().lower())
            if match is not None:
                entry["description"] = match["description"]
                matched_keys.add(str(entry.get("label", "")).strip().lower())
        reconciled.append(entry)

    next_order = len(reconciled)
    for item in derived:
        key = item["label"].strip().lower()
        if key in matched_keys or key in {
            str(e.get("label", "")).strip().lower() for e in existing
        }:
            continue
        reconciled.append(_key_message(item["label"], item["description"],
                                       order=next_order, id_factory=id_factory))
        next_order += 1

    for order, entry in enumerate(reconciled):
        entry["order"] = order
    return (grounded, reconciled) if include_grounded else reconciled
