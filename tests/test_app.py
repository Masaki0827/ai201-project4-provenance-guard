import os
import sqlite3
import tempfile
import unittest

from app import TRANSPARENCY_LABELS, create_app, generate_transparency_label


class ProvenanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.app = create_app({"TESTING": True, "DATABASE_PATH": self.db_path, "USE_GROQ": False})
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_submit_returns_structured_result(self):
        response = self.client.post(
            "/submit",
            json={
                "text": "A quiet morning in the garden was full of soft birdsong and patient light.",
                "creator_id": "creator-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("content_id", payload)
        self.assertIn("decision", payload)
        self.assertIn("confidence", payload)
        self.assertIn("transparency_label", payload)
        self.assertIn("signals", payload)
        self.assertIn("combined_ai_score", payload)
        self.assertEqual(payload["status"], "reviewed")

    def test_transparency_label_thresholds_and_exact_text(self):
        cases = [
            (0.40, "HUMAN"),
            (0.41, "UNCERTAIN"),
            (0.749, "UNCERTAIN"),
            (0.75, "AI"),
        ]
        for score, expected_key in cases:
            with self.subTest(score=score):
                result = generate_transparency_label(score)
                self.assertEqual(result["label_key"], expected_key)
                self.assertEqual(result["reader_label"], TRANSPARENCY_LABELS[expected_key])

    def test_all_three_labels_are_reachable_through_submit(self):
        inputs = {
            "HUMAN": "I remember my coffee by the garden window. My old door stuck after rain, and we laughed about it.",
            "UNCERTAIN": "A careful report explains several practical ideas. Each section offers context and a measured conclusion.",
            "AI": "beautiful journey future world remarkable inspiring " * 20,
        }
        labels = {}
        for expected, text in inputs.items():
            response = self.client.post("/submit", json={"text": text, "creator_id": expected.lower()})
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            labels[expected] = payload["transparency_label"]
            if expected == "HUMAN":
                appealed_content_id = payload["content_id"]
        self.assertEqual(set(labels.values()), set(TRANSPARENCY_LABELS.values()))

        appeal = self.client.post(
            "/appeal",
            json={
                "content_id": appealed_content_id,
                "creator_reasoning": "I wrote this from my own experience.",
            },
        )
        self.assertEqual(appeal.status_code, 200)
        events = self.client.get("/log").get_json()
        self.assertEqual(len(events), 4)
        self.assertEqual(sum(event["event_type"] == "submission" for event in events), 3)
        self.assertEqual(sum(event["event_type"] == "appeal" for event in events), 1)

    def test_appeal_updates_status_and_logs_event(self):
        submit_response = self.client.post(
            "/submit",
            json={"text": "I wrote this myself with a very personal voice and careful detail.", "creator_id": "creator-2"},
        )
        content_id = submit_response.get_json()["content_id"]

        appeal_response = self.client.post(
            "/appeal",
            json={"content_id": content_id, "creator_reasoning": "This sounds like my own lived experience and the system seems uncertain."},
        )

        self.assertEqual(appeal_response.status_code, 200)
        appeal_payload = appeal_response.get_json()
        self.assertEqual(appeal_payload["status"], "under_review")
        self.assertIn("appeal_logged", appeal_payload)

        with sqlite3.connect(self.db_path) as db:
            stored_status = db.execute(
                "SELECT status FROM submissions WHERE id = ?", (content_id,)
            ).fetchone()[0]
            stored_reasoning = db.execute(
                "SELECT reasoning FROM appeals WHERE content_id = ?", (content_id,)
            ).fetchone()[0]
        self.assertEqual(stored_status, "under_review")
        self.assertEqual(
            stored_reasoning,
            "This sounds like my own lived experience and the system seems uncertain.",
        )

        log_response = self.client.get("/log")
        self.assertEqual(log_response.status_code, 200)
        log_payload = log_response.get_json()
        self.assertEqual(len(log_payload), 2)
        appeal_event = log_payload[0]
        self.assertEqual(appeal_event["status"], "under_review")
        self.assertTrue(appeal_event["appeal_filed"])
        self.assertEqual(
            appeal_event["appeal_reasoning"],
            "This sounds like my own lived experience and the system seems uncertain.",
        )
        for required_field in (
            "timestamp",
            "content_id",
            "attribution_result",
            "confidence_score",
            "semantic_score",
            "stylometric_score",
            "content_excerpt",
        ):
            self.assertIsNotNone(appeal_event[required_field])

    def test_submit_rate_limit_allows_ten_then_returns_429(self):
        statuses = [
            self.client.post("/submit", json={"text": f"Rate limit test submission {i}."}).status_code
            for i in range(12)
        ]
        self.assertEqual(statuses, [200] * 10 + [429] * 2)

    def test_rate_limit_does_not_apply_to_log(self):
        statuses = [self.client.get("/log").status_code for _ in range(12)]
        self.assertEqual(statuses, [200] * 12)


if __name__ == "__main__":
    unittest.main()
