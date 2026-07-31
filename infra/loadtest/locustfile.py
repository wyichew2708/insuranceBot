"""Load test (§10.2): 50 concurrent sessions, p95 < 6s per RAG turn
(excluding model cold start).

Run:  locust -f infra/loadtest/locustfile.py --host http://localhost:8000 \
        --users 50 --spawn-rate 10 --run-time 3m --headless \
        --csv .eval-reports/loadtest
Commit the generated CSV summary as the load-test report.
"""

import random
import uuid

from locust import HttpUser, between, task

QUESTIONS = [
    "Does travel insurance cover pre-existing conditions?",
    "How do I update my address?",
    "Is my baggage covered if the airline loses it?",
    "Compare the travel plans for me",
    "How do I submit a travel claim?",
    "What does the travel plan cover?",
    "How do I contact customer service?",
]


class ChatUser(HttpUser):
    wait_time = between(1, 5)

    def on_start(self) -> None:
        self.session_id = uuid.uuid4().hex

    @task(10)
    def chat_turn(self) -> None:
        with self.client.post(
            "/v1/chat",
            json={
                "session_id": self.session_id,
                "brand": "tiq",
                "audience": "public",
                "message": random.choice(QUESTIONS),
            },
            stream=True,
            name="/v1/chat",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
                return
            body = b"".join(resp.iter_content(chunk_size=4096))
            if b'"type":"done"' in body or b'"type": "done"' in body:
                resp.success()
            else:
                resp.failure("stream ended without done event")

    @task(1)
    def feedback(self) -> None:
        self.client.post(
            "/v1/feedback",
            json={"session_id": self.session_id, "rating": "up"},
            name="/v1/feedback",
        )
