# Provenance Guard Portfolio Walkthrough

Target length: 2–3 minutes. Start the Flask server and keep a second terminal ready.

## 0:00–0:25 — Problem and architecture

"Provenance Guard is a backend that helps creative platforms add context about whether submitted writing appears human- or AI-generated without pretending detection is certain. A submission runs through a semantic Groq signal and an independent pure-Python stylometric signal. I combine them 65/35, use a deliberately high AI threshold, and preserve an uncertain middle band because a false accusation against a human creator is especially harmful."

Show the Mermaid diagram under `## Architecture` in `planning.md`.

## 0:25–1:05 — Submit contrasting content

Show `POST /submit` with a personal passage, then a uniform synthetic passage. Point out:

- the UUID `content_id`;
- separate semantic and stylometric scores;
- `combined_ai_score` versus label confidence;
- the different reader-facing label text.

"The tests also exercise a middle input, so all human, uncertain, and AI labels are reachable through the real endpoint."

## 1:05–1:35 — Appeal workflow

Copy the human submission's `content_id` into the README appeal command and run it.

"An appeal captures the creator's own reasoning. It does not silently reclassify the work; it changes the status to `under_review` so a person can make the next decision."

Run `curl -s http://localhost:5000/log | python -m json.tool`. Show that the appeal entry contains the excerpt, original attribution, confidence, both signal scores, `appeal_filed: true`, and the reasoning.

## 1:35–2:00 — Production safeguards

Show the README rate-limit evidence.

"Only submissions are limited: ten per minute supports a normal editing burst, while one hundred per day prevents sustained flooding. The actual twelve-request check returns ten 200 responses and then two 429 responses. Audit events are structured in SQLite and submission or appeal state changes are committed with their audit entries."

## 2:00–2:30 — Limitations and close

"This is a transparency aid, not proof of authorship. Repetitive poetry, formal non-native writing, and very short passages can fool these signals. That is why the system communicates uncertainty and includes an appeal path. In production I would add authenticated ownership, Redis-backed limits, calibrated evaluation data, bias monitoring, and a private human-review queue."

End on the README's three exact transparency labels.
