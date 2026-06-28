# Provenance Guard

Provenance Guard is a Flask-based attribution service that classifies short text submissions as likely AI-generated, likely human-authored, or uncertain. The system is designed to communicate uncertainty honestly and to give creators a clear appeal path.

## Architecture

`POST /submit` passes through an IP-based rate limiter, then runs two independent analyzers. Their AI-likelihood scores are combined, mapped to a confidence value and transparency label, and written to SQLite with the audit event before the response is returned. `POST /appeal` finds that stored submission, changes its status to `under_review`, and records the creator's reasoning beside a snapshot of the original decision.

The semantic analyzer receives more weight because it can consider meaning and context; the structural analyzer acts as an independent check. The AI threshold is deliberately high because falsely accusing a human creator is more harmful here than failing to identify some AI-assisted text. The complete flow diagram is in [planning.md](planning.md#architecture).

## What the system does

A submitted passage travels through a two-signal pipeline:

1. A semantic signal checks for personal grounding, concrete detail, and generic phrasing.
2. A stylometric signal looks at vocabulary diversity and sentence-length variation.

These signals are merged into a single confidence score and a plain-language transparency label. Every decision is logged to a structured SQLite audit log, and appeals move the submission to an under-review state.

## Detection signals

- Semantic signal: Groq's `llama-3.3-70b-versatile` assesses whether the passage reads as human- or AI-generated and returns an AI-likelihood score from 0 to 1. It captures meaning and holistic coherence, but can mistake formal, translated, or heavily edited human prose for AI. When no API key is available, a deterministic lexical fallback keeps local development functional; that fallback is a demo substitute, not an equivalent model.
- Stylometric signal: pure Python measures Type-Token Ratio and sentence-length variance. It captures structural regularity independently of meaning, but short poems, repeated refrains, and templated technical writing can look artificially uniform.

Neither signal is treated as proof. Their disagreement is why the system has an uncertain band and an appeal path.

## Confidence scoring and labels

The combined AI score is `0.65 * semantic + 0.35 * stylometric`. Scores from 0.00–0.40 map to `HUMAN`, scores above 0.40 and below 0.75 map to `UNCERTAIN`, and scores from 0.75–1.00 map to `AI`. The deliberately high AI threshold reduces false accusations against human writers.

The API returns both `combined_ai_score` and `confidence`. The first shows direction on the human-to-AI scale; the second shows the strength assigned within the selected label band. A combined score of 0.60 is therefore ambiguous evidence, not "60% proof" that AI wrote the text.

### Score variation evidence

These are actual `/submit` results from the deterministic local pipeline used by the test suite:

| Input | Semantic | Stylometric | Combined AI score | Confidence | Attribution |
| --- | ---: | ---: | ---: | ---: | --- |
| Personal memory: "I remember my coffee by the garden window…" | 0.080 | 0.688 | 0.293 | 0.71 | `likely_human` |
| Synthetic uniform stress case: repeated generic phrase sequence | 0.950 | 0.950 | 0.950 | 0.95 | `likely_ai` |

The deliberately different inputs produce a 0.657 spread in combined score and reach different labels. Tests also submit a middle case and verify that all `HUMAN`, `UNCERTAIN`, and `AI` variants are reachable through the endpoint.

The three transparency labels are:

- `HUMAN`: "Verified Human Author • This text demonstrates the natural variance, structural rhythm, and stylistic fingerprints characteristic of human writing."
- `UNCERTAIN`: "Mixed Attributed Signal • Analysis of this text yields ambiguous results. It contains stylistic balances found in both human prose and machine-assisted writing."
- `AI`: "Automated Content Signature • Our automated system detected strong patterns and uniformities typical of AI generation. Displayed for platform transparency."

## API surface

- POST /submit: accepts a JSON object with text and an optional creator_id.
- POST /appeal: accepts `content_id` and `creator_reasoning`.
- GET /log: returns the structured audit log.
- GET /health: returns a simple health check.

## Appeals

An accepted appeal changes the stored submission status to `under_review`, stores the creator's reasoning, and writes an appeal event containing a snapshot of the original attribution and signal scores. For example:

```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id":"PASTE-CONTENT-ID-HERE","creator_reasoning":"I wrote this myself from personal experience."}' \
  | python -m json.tool
```

## Rate limiting

Only `POST /submit` is limited, per client IP, to **10 requests per minute and 100 requests per day**. Ten per minute permits normal editing and resubmission bursts; the daily cap is generous for an individual writer but prevents sustained automated flooding. `GET /log`, `POST /appeal`, and health checks are not subject to this submission limit. Local development uses Flask-Limiter's `memory://` backend; production deployments should use a shared persistent backend such as Redis.

The 12-request verification produced the required 429 responses:

```text
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit log

Every real decision and appeal is stored as structured JSON in SQLite; no placeholder events are generated. `GET /log` returns `timestamp`, `content_id`, `content_excerpt`, `attribution_result`, `confidence_score`, `semantic_score`, `stylometric_score`, `combined_ai_score`, `status`, `appeal_filed`, and `appeal_reasoning`. Appeal entries preserve the original classification scores beside the creator's reasoning.

The following three entries are actual `GET /log` output: two submissions followed by an appeal. The appeal repeats the original decision and both signal scores, changes the status to `under_review`, and populates `appeal_reasoning`.

```json
[
  {
    "event_type": "appeal",
    "content_id": "5297c4c4-7b15-4054-970f-5e9d832fe5c2",
    "content_excerpt": "I remember my coffee by the garden window. My old door stuck after rain, and we laughed about it.",
    "attribution_result": "likely_human",
    "confidence_score": 0.71,
    "semantic_score": 0.08,
    "stylometric_score": 0.688,
    "combined_ai_score": 0.293,
    "status": "under_review",
    "appeal_filed": true,
    "appeal_reasoning": "I wrote this myself from personal experience.",
    "timestamp": "2026-06-28T08:36:36Z"
  },
  {
    "event_type": "submission",
    "content_id": "4a79895d-57ff-4408-ab83-232518a2b792",
    "content_excerpt": "beautiful journey future world remarkable inspiring…",
    "attribution_result": "likely_ai",
    "confidence_score": 0.95,
    "semantic_score": 0.95,
    "stylometric_score": 0.95,
    "combined_ai_score": 0.95,
    "status": "reviewed",
    "appeal_filed": false,
    "appeal_reasoning": null,
    "timestamp": "2026-06-28T08:36:36Z"
  },
  {
    "event_type": "submission",
    "content_id": "5297c4c4-7b15-4054-970f-5e9d832fe5c2",
    "content_excerpt": "I remember my coffee by the garden window. My old door stuck after rain, and we laughed about it.",
    "attribution_result": "likely_human",
    "confidence_score": 0.71,
    "semantic_score": 0.08,
    "stylometric_score": 0.688,
    "combined_ai_score": 0.293,
    "status": "reviewed",
    "appeal_filed": false,
    "appeal_reasoning": null,
    "timestamp": "2026-06-28T08:36:36Z"
  }
]
```

## Known limitations

- A minimalist human poem with repeated words and equal-length lines may receive an AI-like stylometric score because low vocabulary diversity and uniform length are exactly what that signal measures.
- Formal writing by a non-native English speaker may look generic or unusually consistent to the semantic model, creating a false positive. The uncertain band, high AI threshold, and appeal workflow reduce the harm but do not solve the detection problem.
- Very short text provides too little evidence for stable type-token and sentence-variance estimates.
- The local lexical fallback is designed for deterministic development and demonstration. Production decisions should use a validated model, calibration data, and human review rather than treating these scores as authorship proof.

## Spec reflection

Writing the thresholds and exact label strings before implementation prevented the scoring code from collapsing into a binary 0.5 cutoff. The tests now assert the 0.40 and 0.75 boundaries and compare every returned label with the verbatim specification.

One implementation choice diverged from the original draft: the draft assumed a verified `creator_id` during appeal, while the required endpoint contract only supplies `content_id` and `creator_reasoning`. This prototype follows that contract and treats possession of the UUID as sufficient; the planning spec was updated to identify authentication and ownership checks as production work. A deterministic fallback was also added so local testing does not depend on Groq availability.

## AI usage

I used Codex as an implementation assistant, but treated `planning.md` as the source of truth and reviewed generated code against the written thresholds and API contract.

| Instance | What I directed the AI to do | What the AI produced | What I reviewed, revised, or overrode |
| --- | --- | --- | --- |
| Milestone 3: API and semantic signal | I supplied the architecture diagram and semantic-signal specification and requested a Flask application factory, a `POST /submit` route, and a Groq-based analyzer returning a 0–1 score. | It produced the initial Flask route structure, Groq request code, score parsing, and SQLite persistence skeleton. | I added strict JSON validation, UUID content IDs, bounded score handling, deterministic fallback behavior, and an atomic submission-plus-audit-log transaction. I also fixed the valid Groq score `0.5` so it is not mistaken for an API failure. |
| Milestone 4: stylometrics and scoring | I supplied the detection-signal and uncertainty sections and requested pure-Python Type-Token Ratio, sentence-length variance, and the documented 65/35 weighted combination. | It produced the token/sentence parser, stylometric calculations, and combined-score mapping. | I checked the formula against `planning.md`, kept the high `0.75` AI threshold to reduce false positives, separated `combined_ai_score` from label confidence, and added boundary and endpoint tests proving all three outcomes are reachable. |
| Milestone 5: production layer | I supplied the exact label strings, appeal workflow, and architecture diagram and requested label mapping, `POST /appeal`, Flask-Limiter configuration, and structured logging. | It produced the initial three-way label function, appeal route, limiter decorator, and audit-event structure. | I corrected the contract to `content_id` plus `creator_reasoning`, standardized the status as `under_review`, restricted rate limiting to `/submit`, and expanded appeal events to preserve the original attribution, confidence, both signal scores, and content excerpt. I then verified ten `200` responses followed by two `429` responses. |

The AI accelerated scaffolding and test generation; the threshold policy, false-positive tradeoff, final API contract, validation rules, and revisions above remained explicit project decisions rather than unreviewed generated output.

## What would change for production

The in-memory limiter would move to Redis so limits are shared across workers. SQLite would move to a managed database with migrations, access-controlled audit views, retention rules, and tamper-evident records. Appeals would require authenticated creator ownership, reviewers would use a private queue, and the detector would be calibrated on representative labeled writing with bias and false-positive monitoring. API timeouts, retries, observability, encryption, and privacy controls would also be required.

## Run locally

1. Create and activate a virtual environment.
2. Install the requirements with pip install -r requirements.txt.
3. Add `GROQ_API_KEY=your_key_here` to a local `.env` file. `.env` is ignored by Git. Without a key, the app uses the documented deterministic fallback.
4. Start the app with `python app.py` (port 5000 by default).
5. Test the endpoints with the Flask test client or curl.

Example submission:

```bash
curl -X POST http://127.0.0.1:5000/submit \
  -H 'Content-Type: application/json' \
  -d '{"text":"A quiet morning in the garden was full of soft birdsong and patient light.","creator_id":"creator-1"}'
```

## Testing

Run the tests with:

```bash
python -m unittest discover -s tests -v
```

The suite verifies exact threshold text, all three labels through `/submit`, the appeal state and audit snapshot, a four-entry structured audit log, and the `200 x 10` then `429 x 2` limiter behavior.

## Portfolio walkthrough

[Watch the Provenance Guard portfolio walkthrough on Google Drive](https://drive.google.com/file/d/1OHQoBh54KMD5jNSoiCLacQY1kxJJPz3j/view?usp=sharing).
