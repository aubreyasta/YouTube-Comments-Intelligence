<!-- impeccable:product-schema 1 -->
# PRODUCT.md

Canonical product specification for YouTube Intelligence. Owns users, purpose, behavior, scope, capabilities, constraints, evidence rules, and product-level acceptance criteria. It does not own visual implementation ([DESIGN.md](DESIGN.md)), delivery phases and status ([PLAN.md](PLAN.md)), or repository commands and coding rules ([CLAUDE.md](CLAUDE.md), [AGENTS.md](AGENTS.md)).

Product terms are defined once, in [README.md](README.md); the code-identifier mapping is in [AGENTS.md](AGENTS.md#terminology). This document uses only the product terms.

---

## Users

Internal agency staff (an Innocean-style ad agency team: account, strategy, and creative roles) evaluating how a campaign's YouTube videos were received. Everyone who knows the one shared password reads and writes every Session, upload, and report — there are no accounts, roles, or per-user ownership. This is a small trusted internal team, not a client-facing or public product.

## Purpose

Put a campaign's YouTube videos in. Get a reception report out.

The tool answers two questions separately, from two disjoint sources, and then joins them:

1. **What did the campaign push?** Read only from the campaign's own material: video transcripts, titles, descriptions, and the briefs, articles, and images the user uploads as User Inputs. Never from comments.
2. **What did the audience talk about?** Read only from comments. Never from campaign material.

Keeping the two reads apart is the product's central premise. If the model saw the comments while describing the campaign, "did the message land" becomes circular and always answers yes. A Key Message with a low travel score **did not arrive** — a different diagnosis from being rejected. The fix for the first is execution and media; the fix for the second is the idea itself.

## Product flow

1. **Set up a Session.** Name it, paste YouTube video links, and add User Inputs (PDF/DOCX/PPTX briefs, article URLs, campaign images). Each User Input is read as it is added, and drafted Key Messages appear immediately so the user can see whether the tool understood the campaign before committing to a run.
2. **Review the Key Messages.** The run pauses after collecting transcripts, which can sharpen or add to the draft. The user edits wording, excludes wrong ones, and confirms. Nothing is measured against a Key Message the user did not approve. (Users may also add, edit, include, exclude, delete, and reorder Key Messages during setup, before a run starts.)
3. **Run the analysis.** In order: scrape comments and transcripts, build a Theme book from a comment sample, then classify every comment with one Theme, zero or more Key Message mentions, one Sentiment, and one Emotion. Percentages are counted afterward, in Python, over the per-comment labels.
4. **Read the results.** Key Message travel as percentages with a positive/negative split, the Theme mix, overall Sentiment, overall Emotions, and a written summary. Every percentage is clickable and shows the comments behind it (evidence).

## Capabilities: what you get

Six public files per completed run:

| File | For |
|---|---|
| `report.pdf` | The debrief. Same content as the results screen. |
| `comments.csv` | Every cleaned comment with its Theme, Key Messages, Sentiment, Emotion, likes, and language. The file for handpicking quotes. |
| `key-messages.csv` | Travel percentages per Key Message. |
| `themes.csv` | Theme frequencies. |
| `sentiment.csv` | Sentiment breakdown. |
| `emotions.csv` | Emotion breakdown. |

The four small CSVs are shaped to drop straight into a slide deck or chart tool; the product draws no charts of its own, because the design team builds their own. `report.pdf` is an internal debrief, not a client deliverable.

## Grounding and evidence rules

These rules are product principles, not implementation details, and hold regardless of which model or deployment serves the product:

- **Grounded-only Key Messages.** Every Key Message claim traces to something the user provided (transcripts, titles, descriptions, uploaded documents, uploaded images). The product never asks the model what it already knows about a campaign from memory — taglines, unit counts, and launch dates are exactly what a model invents fluently.
- **Counting happens in Python, never the model.** Every percentage is counted over per-comment labels. The model never emits a statistic directly, because a number with no per-comment label behind it cannot be checked, and models are poor at counting over large sets.
- **Evidence is a fixed rule, not a curated selection.** For each metric, the tool takes up to eight comments carrying that label, ranked by likes and then by length. Nobody chooses quotes to fit a story.
- **Disabled-feature honesty.** A control that cannot do what it looks like it does must be visibly disabled and explain why. The product never silently no-ops and never fakes an unbuilt feature.

## Scope

**Shipped:** Session setup with videos and User Inputs; grounded Key Message drafting and user review/edit before measurement; one merged classification pass producing Theme, Key Message mentions, Sentiment, and Emotion per comment; Python-counted results with per-metric evidence; six public downloadable artifacts; single shared HTTP Basic Auth password gating one shared workspace.

**Out of scope (by product decision, not yet-missing):**

- Accounts, invitations, per-user ownership, roles, per-Session authorization, audit logs, password reset, or self-service password changes. One shared password, one shared workspace.
- Chat, global search, source discovery, OCR, custom lenses, cross-Session/cross-group reports, and run history. Only the latest run per Session is kept; there is no run history.
- Calibrated confidence scores or a confidence column in any export. An LLM's self-reported confidence is not a calibrated probability.
- Multiple concurrent analyses, a run queue, cancellation, or per-user quotas. One analysis runs at a time; a second start attempt is rejected, not queued.
- Backups, replication, or recovery of an in-progress run after the service restarts. Closing a browser tab does not stop a run; a service restart does.

## Constraints and limits

- **Language.** Indonesian, English, and mixed Indonesian-English comments are supported.
- **Minimum volume.** Under about 100 comments, percentages are not reliable; the report says so per Session.
- **Sentiment/Emotion are per-comment, context-free.** Sarcasm and measured criticism both tend to read as anger. The Theme mix is the better answer to "how was this received"; Emotion answers "the client asked for sentiment."
- **Caption fallback.** A video with no captions falls back to its title and description, and the report flags this.
- **Commenters are not buyers.** Results are directional qualitative input, not market research.
- **Single shared workspace.** Every authenticated person reads and can act on every Session, upload, and report. There is no per-user or per-client isolation.

## Non-goals

- Drawing charts inside the product (the four small CSVs exist so an external tool or the design team can).
- Answering "will this campaign sell," a market-research question this tool does not attempt.
- Serving more than one organization or client workspace from a single deployment.

## Product-level acceptance criteria

- Every percentage in a report traces to labeled rows in that run's `comments.csv` — none is model-emitted.
- No Key Message wording or inclusion is ever influenced by comment content; no Theme, Sentiment, or Emotion label is ever influenced by campaign material.
- A user can see and approve every Key Message before any measurement happens against it.
- Every percentage in the results view opens to the exact evidence comments that produced it.
- All six public artifacts open in their native application (PDF viewer, spreadsheet tool) with the documented column contract.
