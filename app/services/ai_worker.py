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

def _callback_url() -> str:
    return f"{settings.WEB_SERVER_BASE_URL.rstrip('/')}/api/internal/generation-callback"


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
        seed: int | None = None,
        ollama_model_override: str | None = None,
        backend: str = "local",
        cloud_model: str | None = None,
        reference_card_id: int | None = None,
        reference_image_url: str | None = None,
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
        backend : str
            "local" (default) for sd-cli, "cloud" for OpenAI gpt-image-2.
        cloud_model : str | None
            Override cloud model id (testing); ai-worker uses its default if None.
        reference_card_id : int | None
            Phase 1b: anchor card ID (informational only, ai-worker logs it).
        reference_image_url : str | None
            Phase 1b: full URL of the anchor card's image. When provided
            together with backend='cloud', ai-worker will use ``images.edit``
            for character consistency. Required for cloud edit mode; ignored
            for local backend.
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
        seed: int | None = None,
        ollama_model_override: str | None = None,
        backend: str = "local",
        cloud_model: str | None = None,
        reference_card_id: int | None = None,
        reference_image_url: str | None = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        payload = {
            "job_id": job_id,
            "card_id": card_id,
            "student_id": student_id,
            "student_nickname": student_nickname,
            "card_config": card_config,
            "learning_data": learning_data,
            # 空字串：style_profile 系統（7 種雲端 / 3 種本地隨機抽）已負責主風格，
            # 過去寫死的 Hearthstone 字串太強勢會 override profile 多樣性。雲端 v2
            # render 已忽略此欄位，本地 SD 在 style_hint 為空時也不會拼接，profile
            # 多樣性即可正常呈現。未來若要由 admin 自訂 hint，從這裡傳入即可。
            "style_hint": "",
            "seed": seed,
            "ollama_model_override": ollama_model_override,
            "callback_url": _callback_url(),
            "backend": backend,
            "cloud_model": cloud_model,
            "reference_card_id": reference_card_id,
            "reference_image_url": reference_image_url,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/generate", json=payload
                )
                if resp.status_code >= 400:
                    logger.error(
                        "ai-worker returned %d for card %d. payload=%s response=%s",
                        resp.status_code, card_id, payload, resp.text,
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
        seed: int | None = None,
        ollama_model_override: str | None = None,
        backend: str = "local",
        cloud_model: str | None = None,
        reference_card_id: int | None = None,
        reference_image_url: str | None = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "ollama_model_override": ollama_model_override,
            "status": "generating",
            "card_id": card_id,
            "student_id": student_id,
            "student_nickname": student_nickname,
            "card_config": card_config,
            "requested_seed": seed,
            "backend": backend,
            "cloud_model": cloud_model,
            "reference_card_id": reference_card_id,
            "reference_image_url": reference_image_url,
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
        if job_id not in self._jobs:
            return
        generated_at = datetime.now(timezone.utc).isoformat()
        # Use local static placeholder so dev environment can display the image
        _placeholders = [
            "card-copper-1.png",
            "card-silver-1.png",
            "card-silver-2.png",
            "card-gold-1.png",
        ]
        _filename = _placeholders[card_id % len(_placeholders)]
        image_path = f"/static/images/placeholder/{_filename}"
        thumbnail_path = image_path
        self._jobs[job_id].update(
            {
                "status": "completed",
                "image_path": image_path,
                "thumbnail_path": thumbnail_path,
                "generated_at": generated_at,
            }
        )
        logger.info("Mock ai-worker: job %s completed, firing callback", job_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    _callback_url(),
                    json={
                        "job_id": job_id,
                        "card_id": card_id,
                        "status": "completed",
                        "image_path": image_path,
                        "thumbnail_path": thumbnail_path,
                        "generated_at": generated_at,
                        "seed": self._jobs[job_id].get("requested_seed"),
                    },
                )
        except httpx.HTTPError as e:
            logger.error("Mock ai-worker: callback failed for job %s: %s", job_id, e)

    async def check_job_status(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return {"status": "not_found", "error": f"Job {job_id} not found"}
        # Count how many jobs are ahead of this one (queued or generating before it)
        all_jobs = list(self._jobs.values())
        generating_jobs = [j for j in all_jobs if j["status"] == "generating"]
        position = next(
            (i + 1 for i, j in enumerate(generating_jobs) if j is job),
            0,
        )
        result: dict = {
            "job_id": job_id,
            "status": job["status"],
            "card_id": job["card_id"],
            "image_path": job["image_path"],
            "thumbnail_path": job["thumbnail_path"],
            "generated_at": job["generated_at"],
        }
        if job["status"] == "generating":
            result["position"] = position
            result["estimated_seconds"] = position * 30
        return result


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
