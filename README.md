# YouTube Comment Intelligence

Put a campaign's YouTube videos in. Get a reception report out.

The tool answers two questions separately and then joins them:

1. **What did the campaign push?** Read from the campaign's own material: video transcripts, titles, descriptions, and the briefs, articles, and images you upload. Never from the comments.
2. **What did the audience talk about?** Read from the comments. Never from the campaign material.

Keeping them apart is the point. If the model saw the comments while describing the campaign, "did the message land" becomes circular and always answers yes.

A Key Message with a low travel score **did not arrive**, which is a different diagnosis from being rejected. The fix for the first is execution and media. The fix for the second is the idea.

---

## Terms

These are the only words used in this repo's prose. Code identifiers still carry older names in places.

| Term | Means |
|---|---|
| Session | One campaign under analysis. Name, videos, and User Inputs. One session, one campaign. |
| Videos | The YouTube videos you add to a Session. |
| User Inputs | Briefs, articles, and images you upload so the tool knows what the campaign was trying to do. |
| Key Messages | What the campaign pushes. Drafted from your User Inputs as you add them, updated with the video transcripts when the run starts, then reviewed by you before labelling. |
| Theme book | The list of Themes the LLM discovers from a sample of the comments. |
| Themes | What the comments talk about. One Theme per comment. |
| Sentiment | Positive, neutral, negative. |
| Emotions | Joy, anger, sadness, fear, or other/neutral. |
| Travel | The share of comments that mention a given Key Message. |

---

## The flow

**Set up a Session.** Give it a name and paste YouTube links. Add User Inputs: PDF or DOCX or PPTX briefs, article URLs, campaign images. Each one is read as you add it, and the Key Messages appear on the page straight away, so you can see whether the tool understood the campaign before committing to a run.

**Review the Key Messages.** The run pauses after collecting the transcripts, which can sharpen or add to the draft. Edit the wording, exclude the ones that are wrong, confirm. Nothing gets measured against a Key Message you did not approve.

**Run.** In order: scrape comments and transcripts, build the Theme book from a sample, then use one Qwen classification pass to label every comment with one Theme, zero or more Key Messages, one Sentiment, and one Emotion. Python validates the labels and counts the results.

**Read the results.** Key Message travel as percentages with a positive and negative split, the Theme mix, overall Sentiment, overall Emotions, and a written summary. Every percentage is clickable and shows the comments behind it.

---

## Where the numbers come from

Percentages are counted in Python over per-comment labels. The model never produces a statistic directly. Models are poor at counting over large sets, and a number with no per-comment label behind it cannot be checked. Every figure in the report traces back to rows in `comments.csv`.

Evidence works the same way. For each metric the tool takes up to eight comments that carry that label, ranked by likes and then by length. It is a rule, not a selection, so nobody is choosing quotes to fit a story.

---

## What you get

Six files per run.

| File | For |
|---|---|
| `report.pdf` | The debrief. Same content as the results screen. |
| `comments.csv` | Every cleaned comment with its Theme, Key Messages, Sentiment, Emotion, likes, and language. This is the file for handpicking quotes. |
| `key-messages.csv` | Travel percentages per Key Message. |
| `themes.csv` | Theme frequencies. |
| `sentiment.csv` | Sentiment breakdown. |
| `emotions.csv` | Emotion breakdown. |

The four small CSVs are shaped to be dropped straight into Google Slides or any chart tool. The pipeline draws no charts of its own on purpose, because the design team builds their own.

`report.pdf` is an internal debrief, not a client deliverable.

---

## Running it

The tool is a local web app. Start the server, open the browser, work from there.

```bash
pip install -r requirements.txt -r requirements-server.txt
playwright install chromium
python server.py
# http://localhost:8000
```

The server binds loopback and protects every route with one shared HTTP Basic Auth password. A free Cloudflare quick tunnel publishes the service without exposing a local port directly.

There is also a CLI (`python run.py`, configured through `config.py`). It is for debugging the pipeline without the web layer. It is not the product and it is not maintained to the same standard.

Full installation, model setup, deployment, and troubleshooting: [docs/setup.md](docs/setup.md).

---

## The model

The approved deployment runs a multimodal `Qwen3.8-27B` 4-bit MLX build through LM Studio on a MacBook Pro M1 Max with 32 GB of unified memory. The model drafts grounded Key Messages from User Inputs and labels every comment with a Theme, Key Message mentions, Sentiment, and Emotion. Nothing is sent to an external model API.

Python validates every label and counts every percentage. The model never emits report statistics directly.

Local inference has no per-run API cost and keeps client material on the Mac. Throughput depends on the model build, context, corpus, and current unified-memory pressure.

---

## Limits

- Sentiment and Emotion labels are assigned per comment with no surrounding context. Sarcasm and measured criticism both read as anger. The Theme mix is the better answer to "how was this received". Emotion is the answer to "the client asked for sentiment".
- A video with no captions falls back to its title and description, flagged in the report.
- Under about 100 comments the percentages are not reliable. The report says so per Session.
- Commenters are not buyers. This is directional qualitative input, not market research.

---

## Where the code is today

The pipeline, backend, frontend, Basic Auth, six public artifacts, merged classification pass, quick-tunnel deployment, and LM Studio `LLM_*` provider boundary are shipped.

Chat, source discovery, OCR, custom lenses, and run history are out of scope. Disabled controls remain disabled rather than pretending those features exist.

---

## Docs

- [docs/setup.md](docs/setup.md) install, configure, run, troubleshoot.
- [docs/deployment.md](docs/deployment.md) prepare and accept the shared Mac service.
- [docs/architecture.md](docs/architecture.md) how the pipeline, backend, and frontend fit together.
- [docs/api-reference.md](docs/api-reference.md) the HTTP contract.