"""
The only file you normally need to edit.

Put your links in VIDEOS, set the YouTube API key, run `python run.py`.
"""

# ---------------------------------------------------------------- inputs
# One entry per video. "group" is how results get compared: give two videos
# the same group name and they are treated as one campaign.
#
# "kind" tells the pipeline what it is looking at:
#   "brand_ad"  - the brand's own upload. Looks for creative decisions.
#   "review"    - an independent creator. Looks for which features landed.
#   "explainer" - news, commentary, a viral moment. Looks for what people
#                 latched onto.
# If you are not sure, use "auto" and the pipeline will decide from the
# transcript.

# Videos are currently set to one World Cup ads campaign.
VIDEOS = [
    {"url": "https://www.youtube.com/watch?v=IyZ1WIua_1s",
     "group": "World Cup Ads", "kind": "brand_ad"},
    {"url": "https://www.youtube.com/watch?v=yu4XecZWFuE",
     "group": "World Cup Ads", "kind": "review"},
    {"url": "https://www.youtube.com/watch?v=mJJY53qhJe0",
     "group": "World Cup Ads", "kind": "brand_ad"},
    {"url": "https://www.youtube.com/watch?v=lRzZe8S3tww",
     "group": "World Cup Ads", "kind": "review"},
]

# Name for this run's output folder. Leave blank to derive it from the
# Campaign name above, e.g. "world-cup-ads".
SESSION_NAME = ""

# ---------------------------------------------------------------- keys
YOUTUBE_API_KEY = "ENTER YOUTUBE DATA API KEY"

# ---------------------------------------------------------- local Ollama/Qwen
# Ollama must be running locally with both exact tags installed. The pipeline
# never pulls models automatically:
#   ollama pull qwen3:14b-q4_K_M
#   ollama pull qwen3-vl:8b-instruct-q4_K_M
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
TEXT_MODEL = "qwen3:14b-q4_K_M"
VISION_MODEL = "qwen3-vl:8b-instruct-q4_K_M"
OLLAMA_TEXT_NUM_CTX = 32768
OLLAMA_VISION_NUM_CTX = 8192
OLLAMA_TIMEOUT_SECONDS = 600
OLLAMA_KEEP_ALIVE = "10m"

# ---------------------------------------------------------------- filters
# Languages to keep. Add codes as needed: id=Indonesian, ms=Malay,
# en=English, tl=Tagalog (langdetect often tags Indonesian slang as tl).
# Set to None to keep everything.
KEEP_LANGUAGES = {"id", "ms", "en", "tl"}

MIN_COMMENT_LETTERS = 8      # below this, there is nothing to analyse
MAX_COMMENTS_PER_VIDEO = 2000

# Theme discovery sampling. The sample is used only to build the THEME
# SCHEMA (themes + definitions) that the LLM applies per comment as an
# enum. Percentages are computed over the full corpus, so the sample
# does not need to be proportionally accurate, only broad enough to
# contain an example of every real theme.
#
# Actual size used = max(CODEBOOK_SAMPLE_SIZE, 8% of corpus), capped at
# CODEBOOK_SAMPLE_MAX. Target 500-800 on the Pro tier.
# So 400 comments -> 150, 5,000 comments -> 500.
CODEBOOK_SAMPLE_SIZE = 150
CODEBOOK_SAMPLE_MAX = 500

# Classification batch size. Each batch is one LLM call; smaller batches
# are more accurate per item but produce more calls. Research shows large
# batches degrade per-item accuracy (position sensitivity). 25 is a
# reasonable default; raise to cut call count at the cost of some accuracy.
CLASSIFY_BATCH_SIZE = 25

# If the "Other" share after classification exceeds this percentage, run
# one extra discovery pass over the Other subset to extend the theme
# schema, then reclassify only that subset. Set to 100 to disable.
UNCLASSIFIED_LIMIT = 30

# ---------------------------------------------------------------- emotion
# Both models always run, locally via HuggingFace, so they cost nothing in
# tokens, but they download ~500 MB the first time and are slow without a GPU.
#   emotion   -> happy / anger / sadness / fear / love
#   sentiment -> positive / neutral / negative
EMOTION_MODEL = "StevenLimcorn/indonesian-roberta-base-emotion-classifier"
SENTIMENT_MODEL = "w11wo/indonesian-roberta-base-sentiment-classifier"

# Optional hints to steer the grounded brief. Keys must match "group".
CAMPAIGN_CONTEXT = {
    # "Campaign A": "Indonesian launch, mid-size SUV, black special edition",
}

# Language the final report is written in. The comments stay in their
# original language; this only controls the prose around them.
REPORT_LANGUAGE = "English"

OUTPUT_DIR = "output"


# By default the run produces six files: report.pdf, comments.csv,
# key-messages.csv, themes.csv, sentiment.csv, and emotions.csv. Turn this
# on to also write working files into output/debug/ for auditing - see
# docs/setup.md#running for what's in it and which file is worth
# checking first when a number looks wrong.
KEEP_INTERMEDIATE = False
