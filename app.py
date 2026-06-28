import json
import os
import re
import sqlite3
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Flask, g, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

load_dotenv()


TRANSPARENCY_LABELS = {
    "HUMAN": "Verified Human Author • This text demonstrates the natural variance, structural rhythm, and stylistic fingerprints characteristic of human writing.",
    "UNCERTAIN": "Mixed Attributed Signal • Analysis of this text yields ambiguous results. It contains stylistic balances found in both human prose and machine-assisted writing.",
    "AI": "Automated Content Signature • Our automated system detected strong patterns and uniformities typical of AI generation. Displayed for platform transparency.",
}


def generate_transparency_label(final_ai_score: float) -> Dict[str, Any]:
    """Map a 0..1 AI score to the exact label and confidence policy in planning.md."""
    if not 0.0 <= final_ai_score <= 1.0:
        raise ValueError("final_ai_score must be between 0.0 and 1.0")

    if final_ai_score <= 0.40:
        label_key = "HUMAN"
        confidence = round(1.0 - final_ai_score, 2)
    elif final_ai_score < 0.75:
        label_key = "UNCERTAIN"
        confidence = round(0.5 + (final_ai_score - 0.4) * (0.5 / 0.35), 2)
    else:
        label_key = "AI"
        confidence = round(final_ai_score, 2)

    return {
        "label_key": label_key,
        "confidence": confidence,
        "reader_label": TRANSPARENCY_LABELS[label_key],
    }


def create_app(test_config: Optional[Dict[str, Any]] = None) -> Flask:
    app = Flask(__name__)

    app.config.from_mapping(
        DATABASE_PATH=os.environ.get("DATABASE_PATH", os.path.join(app.root_path, "provenance_guard.db")),
        JSON_SORT_KEYS=False,
        SUBMIT_RATE_LIMIT="10 per minute;100 per day",
        LIMITER_STORAGE_URI="memory://",
        USE_GROQ=True,
    )

    if test_config:
        app.config.update(test_config)

    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[],
        storage_uri=app.config["LIMITER_STORAGE_URI"],
    )

    @app.teardown_appcontext
    def close_db(exc: Optional[BaseException]) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            conn = sqlite3.connect(app.config["DATABASE_PATH"])
            conn.row_factory = sqlite3.Row
            g.db = conn
        return g.db

    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def json_dumps(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True)

    def content_excerpt(text: str, limit: int = 160) -> str:
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    def init_db() -> None:
        db = get_db()
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY,
                creator_id TEXT,
                content TEXT,
                status TEXT,
                decision TEXT,
                confidence REAL,
                transparency_label TEXT,
                signals TEXT,
                created_at TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS appeals (
                id TEXT PRIMARY KEY,
                content_id TEXT,
                reasoning TEXT,
                status TEXT,
                created_at TEXT,
                FOREIGN KEY(content_id) REFERENCES submissions(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                content_id TEXT,
                detail TEXT,
                created_at TEXT
            )
            """
        )
        # Remove placeholder rows created by the pre-production prototype.
        db.execute("DELETE FROM audit_log WHERE event_type = 'seed'")
        db.commit()

    def parse_text(text: str) -> Dict[str, Any]:
        normalized = re.sub(r"\s+", " ", text).strip()
        tokens = re.findall(r"\b[\w']+\b", normalized.lower())
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()]
        sentence_lengths = [len(re.findall(r"\b[\w']+\b", sentence)) for sentence in sentences] if sentences else [0]

        first_person_count = sum(1 for token in tokens if token in {"i", "me", "my", "mine", "we", "our", "us"})
        contraction_count = sum(1 for token in tokens if "'" in token)
        generic_markers = sum(1 for token in tokens if token in {"beautiful", "journey", "future", "world", "delicate", "inspiring", "remarkable"})
        concrete_markers = sum(1 for token in tokens if token in {"garden", "window", "street", "rain", "coffee", "door", "light", "quiet", "soft", "birdsong"})
        unique_ratio = len(set(tokens)) / max(1, len(tokens))
        variance = statistics.pvariance(sentence_lengths) if len(sentence_lengths) > 1 else 0

        return {
            "normalized": normalized,
            "tokens": tokens,
            "sentences": sentences,
            "sentence_lengths": sentence_lengths,
            "first_person_count": first_person_count,
            "contraction_count": contraction_count,
            "generic_markers": generic_markers,
            "concrete_markers": concrete_markers,
            "unique_ratio": unique_ratio,
            "variance": variance,
        }

    def analyze_semantic(text: str) -> Dict[str, Any]:
        """Return a semantic (LLM-based) score between 0.0 and 1.0 and an explanation."""
        features = parse_text(text)
        semantic_ai_score = 0.5
        external_score_received = False
        semantic_reason = "The semantic signal used lightweight lexical checks because an external LLM call was unavailable."

        groq_api_key = os.environ.get("GROQ_API_KEY")
        if groq_api_key and app.config["USE_GROQ"]:
            try:
                from groq import Groq  # type: ignore

                client = Groq(api_key=groq_api_key)
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    temperature=0.2,
                    max_tokens=40,
                    messages=[
                        {
                            "role": "system",
                            "content": "You assess whether a passage reads as likely AI-generated or human-authored. Respond with one float between 0 and 1 where 0 means human-authored and 1 means AI-generated.",
                        },
                        {"role": "user", "content": text},
                    ],
                )
                raw_answer = getattr(response.choices[0].message, "content", "") or ""
                match = re.search(r"(0(?:\.\d+)?|1(?:\.0+)?)", raw_answer)
                if match:
                    semantic_ai_score = float(match.group(1))
                    external_score_received = True
                    semantic_reason = "The semantic signal used Groq to assess whether the passage reads as more AI-like or more human-like."
            except Exception:
                semantic_ai_score = 0.5
                semantic_reason = "The semantic signal fell back to deterministic heuristics because the Groq call was unavailable."

        if not external_score_received:
            human_markers = features["first_person_count"] + features["contraction_count"] + min(3, features["concrete_markers"])
            ai_markers = features["generic_markers"] + max(0, 2 - features["concrete_markers"])
            semantic_ai_score = min(0.95, max(0.05, 0.5 + (ai_markers - human_markers) * 0.06))
            semantic_reason = "The semantic signal used first-person, contraction, and concrete-detail cues to estimate whether the writing sounded more personal and grounded or more generic."

        return {"score": float(round(semantic_ai_score, 3)), "explanation": semantic_reason}

    def analyze_stylometric(text: str) -> Dict[str, Any]:
        """Return a stylometric score between 0.0 and 1.0 and an explanation.

        Combines type-token ratio and sentence-length variance into a single score.
        Higher values indicate more AI-like characteristics.
        """
        features = parse_text(text)
        # Type-token ratio (TTR): lower diversity -> more AI-like
        ttr = features["unique_ratio"]  # between 0 and 1
        ttr_score = 1.0 - ttr  # higher when less diverse

        # Sentence-length variance: low variance (uniform sentences) -> more AI-like
        variance = features["variance"]
        # Normalize variance into 0..1 using a heuristic cap
        var_norm = max(0.0, min(1.0, variance / 16.0))
        var_score = 1.0 - var_norm

        # Combine the two stylometric indicators
        stylometric_score = min(0.95, max(0.05, 0.5 + (ttr_score * 0.6 + var_score * 0.4) * 0.5))
        stylometric_reason = "The stylometric signal combined vocabulary diversity (TTR) and sentence-length variance to estimate uniformity vs. variability."

        return {"score": float(round(stylometric_score, 3)), "explanation": stylometric_reason}

    def combine_scores(semantic_score: float, stylometric_score: float) -> Dict[str, Any]:
        """Combine two 0..1 scores using the project weighting and map to label + confidence."""
        final_ai_score = float(round((0.65 * semantic_score) + (0.35 * stylometric_score), 3))
        return {"final_ai_score": final_ai_score, **generate_transparency_label(final_ai_score)}

    def classify_text(text: str) -> Dict[str, Any]:
        sem = analyze_semantic(text)
        styl = analyze_stylometric(text)
        combo = combine_scores(sem["score"], styl["score"])

        decision = "uncertain"
        if combo["label_key"] == "AI":
            decision = "likely_ai"
        elif combo["label_key"] == "HUMAN":
            decision = "likely_human"
        else:
            decision = "uncertain"

        return {
            "decision": decision,
            "confidence": combo["confidence"],
            "transparency_label": combo["reader_label"],
            "signals": [
                {"name": "semantic_signal", "score": sem["score"], "explanation": sem["explanation"]},
                {"name": "stylometric_signal", "score": styl["score"], "explanation": styl["explanation"]},
            ],
            "combined_ai_score": combo["final_ai_score"],
        }

    def log_event(event_type: str, content_id: Optional[str], detail: Dict[str, Any]) -> None:
        db = get_db()
        db.execute(
            "INSERT INTO audit_log (event_type, content_id, detail, created_at) VALUES (?, ?, ?, ?)",
            (event_type, content_id, json_dumps(detail), now_iso()),
        )

    with app.app_context():
        init_db()

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    @app.post("/submit")
    @limiter.limit(app.config["SUBMIT_RATE_LIMIT"])
    def submit_content() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400
        text_value = payload.get("text")
        if not isinstance(text_value, str) or not text_value.strip():
            return jsonify({"error": "text is required"}), 400
        text = text_value.strip()

        classification = classify_text(text)
        content_id = str(uuid.uuid4())
        creator_id = str(payload.get("creator_id") or "anonymous")
        db = get_db()
        db.execute(
            """
            INSERT INTO submissions (id, creator_id, content, status, decision, confidence, transparency_label, signals, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_id,
                creator_id,
                text,
                "reviewed",
                classification["decision"],
                classification["confidence"],
                classification["transparency_label"],
                json_dumps(classification["signals"]),
                now_iso(),
            ),
        )
        log_event(
            "submission",
            content_id,
            {
                "creator_id": creator_id,
                "content_excerpt": content_excerpt(text),
                "attribution": classification["decision"],
                "confidence": classification["confidence"],
                "semantic_score": classification["signals"][0]["score"],
                "stylometric_score": classification["signals"][1]["score"],
                "combined_ai_score": classification["combined_ai_score"],
                "status": "reviewed",
                "appeal_filed": False,
                "appeal_reasoning": None,
                "signals": classification["signals"],
            },
        )
        db.commit()

        return jsonify(
            {
                "content_id": content_id,
                "status": "reviewed",
                "decision": classification["decision"],
                "attribution": classification["decision"],
                "confidence": classification["confidence"],
                "label": classification["transparency_label"],
                "transparency_label": classification["transparency_label"],
                "signals": classification["signals"],
                "combined_ai_score": classification["combined_ai_score"],
            }
        )

    @app.post("/appeal")
    def appeal_content() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400
        content_id = payload.get("content_id")
        reasoning_value = payload.get("creator_reasoning")
        if not isinstance(content_id, str) or not isinstance(reasoning_value, str) or not reasoning_value.strip():
            return jsonify({"error": "content_id and creator_reasoning are required"}), 400
        reasoning = reasoning_value.strip()

        db = get_db()
        submission = db.execute(
            "SELECT id, creator_id, content, status, decision, confidence, signals FROM submissions WHERE id = ?",
            (content_id,),
        ).fetchone()
        if submission is None:
            return jsonify({"error": "content_id not found"}), 404

        appeal_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO appeals (id, content_id, reasoning, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (appeal_id, content_id, reasoning, "submitted", now_iso()),
        )
        db.execute("UPDATE submissions SET status = ? WHERE id = ?", ("under_review", content_id))

        signals = json.loads(submission["signals"])

        log_event(
            "appeal",
            content_id,
            {
                "appeal_id": appeal_id,
                "creator_id": submission["creator_id"],
                "content_excerpt": content_excerpt(submission["content"]),
                "attribution": submission["decision"],
                "confidence": submission["confidence"],
                "semantic_score": signals[0]["score"],
                "stylometric_score": signals[1]["score"],
                "combined_ai_score": round(
                    (0.65 * signals[0]["score"]) + (0.35 * signals[1]["score"]), 3
                ),
                "status": "under_review",
                "appeal_filed": True,
                "appeal_reasoning": reasoning,
                "signals": signals,
            },
        )
        db.commit()

        return jsonify(
            {
                "content_id": content_id,
                "status": "under_review",
                "appeal_received": True,
                "appeal_logged": True,
                "appeal_id": appeal_id,
                "message": "Appeal received and queued for human review.",
            }
        )

    @app.get("/log")
    def get_log() -> Any:
        db = get_db()
        rows = db.execute(
            "SELECT event_type, content_id, detail, created_at FROM audit_log ORDER BY id DESC"
        ).fetchall()
        events = []
        for row in rows:
            detail = json.loads(row["detail"])
            signals = detail.get("signals") or []
            scores = {signal.get("name"): signal.get("score") for signal in signals}
            semantic_score = detail.get("semantic_score", detail.get("llm_score"))
            semantic_score = semantic_score if semantic_score is not None else scores.get("semantic_signal")
            stylometric_score = detail.get("stylometric_score")
            stylometric_score = (
                stylometric_score if stylometric_score is not None else scores.get("stylometric_signal")
            )
            combined_ai_score = detail.get("combined_ai_score")
            if combined_ai_score is None and semantic_score is not None and stylometric_score is not None:
                combined_ai_score = round((0.65 * semantic_score) + (0.35 * stylometric_score), 3)
            events.append(
                {
                    "timestamp": row["created_at"],
                    "event_type": row["event_type"],
                    "content_id": row["content_id"],
                    "content_excerpt": detail.get("content_excerpt"),
                    "attribution_result": detail.get("attribution"),
                    "confidence_score": detail.get("confidence"),
                    "semantic_score": semantic_score,
                    "stylometric_score": stylometric_score,
                    "combined_ai_score": combined_ai_score,
                    "status": detail.get("status"),
                    "appeal_filed": detail.get("appeal_filed", False),
                    "appeal_reasoning": detail.get("appeal_reasoning"),
                }
            )
        return jsonify(events)

    @app.errorhandler(429)
    def handle_rate_limit(_: Exception):
        return jsonify({"error": "rate limit exceeded"}), 429

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5000")))
