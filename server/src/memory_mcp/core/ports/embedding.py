"""记忆内容向量化的端口契约。"""

from typing import Protocol


class EmbeddingProvider(Protocol):
    """将文本映射为稠密向量，供语义召回使用。"""

    @property
    def model_id(self) -> str:
        """用于审计的稳定模型标识。"""
        ...

    @property
    def dimensions(self) -> int:
        """输出向量的维度。"""
        ...

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """批量计算 embedding，返回与输入等长的向量序列。

        实现应批量请求以降低网络开销；单条输入也接受。
        如果底层 API 失败，实现应抛异常，由调用方决定降级策略。
        """
        ...


__all__ = ["EmbeddingProvider"]
