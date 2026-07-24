"""
The only file you normally need to edit.

Put your links in VIDEOS, set the two API keys, run `python run.py`.
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

# Videos are currently set to Nike and Adidas World Cup Ad
VIDEOS = [
    {"url": "https://www.youtube.com/watch?v=IyZ1WIua_1s",
     "group": "Nike - Rip the Script", "kind": "brand_ad"},
    {"url": "https://www.youtube.com/watch?v=yu4XecZWFuE",
        "group": "Nike - Rip the Script", "kind": "review"},
    {"url": "https://www.youtube.com/watch?v=mJJY53qhJe0",
    "group": "Adidas - Backyard Legends", "kind": "brand_ad"},
    {"url": "https://www.youtube.com/watch?v=lRzZe8S3tww",
    "group": "Adidas - Backyard Legends", "kind": "review"},    
]

# Name for this run's output folder. Leave blank to derive it from the
# group names above, e.g. "honda-toyota-mitsubishi".
SESSION_NAME = ""

# ---------------------------------------------------------------- keys
YOUTUBE_API_KEY = "ENTER YOUTUBE DATA API KEY"
GEMINI_API_KEY = "ENTER GEMINI API KEY"

# ---------------------------------------------------------------- models
# Model IDs change every few months. If you get a 404, open
# https://ai.google.dev/gemini-api/docs/models and copy the current
# free-tier Flash ID here. Do not assume the name below still exists.
MODEL_CHEAP = "gemini-3.5-flash-lite"   # bulk work: codebook, brief
MODEL_SMART = "gemini-3.6-flash"        # final synthesis only

# ---------------------------------------------------------------- filters
# Languages to keep. Add codes as needed: id=Indonesian, ms=Malay,
# en=English, tl=Tagalog (langdetect often tags Indonesian slang as tl).
# Set to None to keep everything.
KEEP_LANGUAGES = {"id", "ms", "en", "tl"}

MIN_COMMENT_LETTERS = 8      # below this, there is nothing to analyse
MAX_COMMENTS_PER_VIDEO = 2000

# Codebook sampling. The sample is used only to DISCOVER themes; all
# percentages are counted over the full corpus, so the sample does not
# need to be proportionally accurate, only broad enough to contain an
# example of everything worth naming.
#
# Actual size used = max(CODEBOOK_SAMPLE_SIZE, 8% of corpus), capped at
# CODEBOOK_SAMPLE_MAX. So 400 comments -> 150, 5,000 comments -> 400.
CODEBOOK_SAMPLE_SIZE = 150
CODEBOOK_SAMPLE_MAX = 500

# If more than this share of comments match no theme, run one extra
# pass over the leftovers to extend the codebook. Set to 100 to disable.
UNCLASSIFIED_LIMIT = 30

# ---------------------------------------------------------------- emotion
# Always runs. Locally via HuggingFace, so it costs nothing in tokens,
# but it downloads ~500 MB the first time and is slow without a GPU.
#   "emotion"   -> happy / anger / sadness / fear / love
#   "sentiment" -> positive / neutral / negative
EMOTION_MODE = "emotion"

EMOTION_MODEL = "StevenLimcorn/indonesian-roberta-base-emotion-classifier"
SENTIMENT_MODEL = "w11wo/indonesian-roberta-base-sentiment-classifier"

# ---------------------------------------------------------------- background
# Ask the model what it knows about each campaign, with search grounding
# where available. Useful context, but NOT grounded in your comment data,
# so it is written to a separate file and labelled as needing verification.
CAMPAIGN_BACKGROUND = True

# Optional hints to steer the background brief. Keys must match "group".
CAMPAIGN_CONTEXT = {
    # "Campaign A": "Indonesian launch, mid-size SUV, black special edition",
}

# Language the final report is written in. The comments stay in their
# original language; this only controls the prose around them.
REPORT_LANGUAGE = "English"

OUTPUT_DIR = "output"

# By default the run produces three files: report.pdf, comments.csv and
# summary.csv. Turn this on to also write the working files (raw fetch,
# video briefs, codebook, charts) into output/debug/ for auditing.
#
# No stage reads these: data passes between stages in memory. They exist
# only so a human can check the reasoning. The one worth turning on when
# a number looks wrong is the codebook, which contains the exact keyword
# rules behind every percentage.
KEEP_INTERMEDIATE = False