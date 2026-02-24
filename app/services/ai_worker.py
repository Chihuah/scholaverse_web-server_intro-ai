"""AI Worker service interface for communicating with vm-ai-worker."""

import asyncio
import logging
import random
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

WEB_SERVER_CALLBACK_URL = "http://192.168.50.111/api/internal/generation-callback"


class AIWorkerService(ABC):
    """Abstract interface for vm-ai-worker communication."""

    @abstractmethod
    async def submit_generation(
        self,
        card_id: int,
        student_id: str,
        student_nickname: str,
        card_config: dict,
        learning_data: dict,
    ) -> str:
        """Submit learning data + card config to ai-worker for prompt generation
        and image creation. Returns job_id.

        Parameters
        ----------
        card_id : int
            Card row ID in the web-server database.
        student_id : str
            Student ID number (純數字學號). Used by ai-worker as the sd-cli
            seed so the same student always gets a consistent generation base.
        student_nickname : str
            Student's display nickname shown on the card.
        card_config : dict
            RPG attribute configuration.
        learning_data : dict
            Unit scores and overall completion.
        """

    @abstractmethod
    async def check_job_status(self, job_id: str) -> dict:
        """Check generation job status.
        Returns dict with keys: status, image_path, thumbnail_path, etc."""


class RealAIWorkerService(AIWorkerService):
    """Real implementation that calls vm-ai-worker over HTTP."""

    def __init__(self) -> None:
        self._base_url = settings.AI_WORKER_BASE_URL.rstrip("/")

    async def submit_generation(
        self,
        card_id: int,
        student_id: str,
        student_nickname: str,
        card_config: dict,
        learning_data: dict,
    ) -> str:
        job_id = str(uuid.uuid4())
        payload = {
            "job_id": job_id,
            "card_id": card_id,
            "student_id": student_id,
            "student_nickname": student_nickname,
            "card_config": card_config,
            "learning_data": learning_data,
            "style_hint": "16-bit pixel art, fantasy RPG character card",
            "callback_url": WEB_SERVER_CALLBACK_URL,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/generate", json=payload
                )
                resp.raise_for_status()
                return job_id
        except httpx.HTTPError as e:
            logger.error("Failed to submit generation to ai-worker: %s", e)
            raise

    async def check_job_status(self, job_id: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._base_url}/api/jobs/{job_id}"
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as e:
            logger.error("Failed to check job status: %s", e)
            return {"status": "error", "error": str(e)}


class MockAIWorkerService(AIWorkerService):
    """Mock implementation for development without vm-ai-worker."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}

    async def submit_generation(
        self,
        card_id: int,
        student_id: str,
        student_nickname: str,
        card_config: dict,
        learning_data: dict,
    ) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "status": "generating",
            "card_id": card_id,
            "student_id": student_id,
            "card_config": card_config,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "image_path": None,
            "thumbnail_path": None,
            "generated_at": None,
        }
        # Simulate async generation with 3-5 second delay
        asyncio.create_task(self._simulate_generation(job_id, card_id))
        logger.info("Mock ai-worker: job %s submitted for card %d", job_id, card_id)
        return job_id

    async def _simulate_generation(self, job_id: str, card_id: int) -> None:
        delay = random.uniform(3.0, 5.0)
        await asyncio.sleep(delay)
        if job_id in self._jobs:
            self._jobs[job_id].update(
                {
                    "status": "completed",
                    "image_path": f"/students/{card_id}/cards/card_{card_id:03d}.png",
                    "thumbnail_path": f"/students/{card_id}/cards/card_{card_id:03d}_thumb.png",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            logger.info("Mock ai-worker: job %s completed", job_id)

    async def check_job_status(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return {"status": "not_found", "error": f"Job {job_id} not found"}
        return {
            "job_id": job_id,
            "status": job["status"],
            "card_id": job["card_id"],
            "image_path": job["image_path"],
            "thumbnail_path": job["thumbnail_path"],
            "generated_at": job["generated_at"],
        }


# Singleton instances
_ai_worker_service: AIWorkerService | None = None


def get_ai_worker_service() -> AIWorkerService:
    """Factory: returns mock or real AI worker service based on config."""
    global _ai_worker_service
    if _ai_worker_service is None:
        if settings.USE_MOCK_AI_WORKER:
            logger.info("Using MockAIWorkerService")
            _ai_worker_service = MockAIWorkerService()
        else:
            logger.info("Using RealAIWorkerService -> %s", settings.AI_WORKER_BASE_URL)
            _ai_worker_service = RealAIWorkerService()
    return _ai_worker_service
