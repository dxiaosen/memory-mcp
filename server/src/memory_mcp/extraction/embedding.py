"""基于 Qwen DashScope 兼容接口的 embedding 实现。"""

from __future__ import annotations

import logging
from time import perf_counter

import httpx

from memory_mcp.extraction.settings import EmbeddingSettings
from memory_mcp.logging import log_event

_LOGGER = logging.getLogger(__name__)

_MAX_BATCH_SIZE = 64


class QwenEmbeddingProvider:
    """通过 DashScope 兼容 OpenAI 接口计算 embedding。

    使用 httpx 同步批量请求；调用方负责在适当线程中执行。
    API 失败时抛 ``EmbeddingError``，由调用方决定降级策略。
    """

    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings
        self._api_key = settings.require_api_key().get_secret_value()
        self._base_url = settings.base_url.rstrip("/")
        self._model = settings.model_name
        self._dimensions = settings.dimensions
        self._timeout = settings.timeout_seconds
        self._max_retries = settings.max_retries

    @property
    def model_id(self) -> str:
        return f"qwen:{self._model}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """批量计算 embedding；空输入返回空元组。"""

        if not texts:
            return ()
        results: list[tuple[float, ...]] = []
        for start in range(0, len(texts), _MAX_BATCH_SIZE):
            batch = texts[start : start + _MAX_BATCH_SIZE]
            results.extend(self._embed_batch(batch))
        return tuple(results)

    def _embed_batch(self, texts: tuple[str, ...]) -> list[tuple[float, ...]]:
        """请求单个批次，带有限重试。"""

        started_at = perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                response = httpx.post(
                    f"{self._base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "input": list(texts),
                    },
                    timeout=self._timeout,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data", [])
                if len(data) != len(texts):
                    raise EmbeddingError(
                        f"embedding count mismatch: expected {len(texts)}, "
                        f"got {len(data)}"
                    )
                log_event(
                    _LOGGER,
                    logging.DEBUG,
                    "memory.embedding.completed",
                    batch_size=len(texts),
                    duration_ms=round((perf_counter() - started_at) * 1000, 3),
                    model_id=self.model_id,
                )
                return [tuple(item["embedding"]) for item in data]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_error = exc
                if attempt <= self._max_retries:
                    continue
        raise EmbeddingError(
            f"embedding API failed after {self._max_retries + 1} attempts: {last_error}"
        ) from last_error


class EmbeddingError(RuntimeError):
    """embedding 计算失败时抛出的运行时异常。"""


__all__ = ["EmbeddingError", "QwenEmbeddingProvider"]
