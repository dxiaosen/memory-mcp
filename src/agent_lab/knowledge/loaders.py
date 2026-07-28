"""把本地文本及 PDF 文件加载为 LangChain 文档。"""

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path

from langchain_core.documents import Document
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from agent_lab.exceptions import KnowledgeBaseError

_SUPPORTED_SUFFIXES = {".md", ".markdown", ".pdf", ".txt"}


class KnowledgeDocumentLoader:
    """将支持的本地文件加载为 LangChain 文档。"""

    def load(self, paths: Sequence[str | Path]) -> list[Document]:
        """加载文件或递归加载目录中的受支持文档。"""

        files = self._resolve_files(paths)
        documents: list[Document] = []
        for path in files:
            if path.suffix.casefold() == ".pdf":
                documents.extend(self._load_pdf(path))
            else:
                documents.append(self._load_text(path))
        return documents

    def _resolve_files(self, paths: Sequence[str | Path]) -> list[Path]:
        """校验输入路径并展开为有序、去重的文件列表。"""

        if not paths:
            raise KnowledgeBaseError("At least one knowledge path is required.")

        files: set[Path] = set()
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            if not path.exists():
                raise KnowledgeBaseError(f"Knowledge path does not exist: {path}")
            if path.is_dir():
                files.update(self._supported_files_in(path))
            elif path.suffix.casefold() in _SUPPORTED_SUFFIXES:
                files.add(path.resolve())
            else:
                supported = ", ".join(sorted(_SUPPORTED_SUFFIXES))
                raise KnowledgeBaseError(
                    f"Unsupported knowledge file: {path}. Supported: {supported}"
                )

        if not files:
            raise KnowledgeBaseError(
                "No supported .txt, .md, .markdown, or .pdf files were found."
            )
        return sorted(files)

    def _supported_files_in(self, directory: Path) -> Iterable[Path]:
        """递归查找目录中受支持的知识文件。"""

        return (
            candidate.resolve()
            for candidate in directory.rglob("*")
            if candidate.is_file()
            and candidate.suffix.casefold() in _SUPPORTED_SUFFIXES
        )

    def _load_text(self, path: Path) -> Document:
        """加载单个纯文本或 Markdown 文件。"""

        metadata = self._base_metadata(path)
        metadata["page"] = 1
        return Document(
            page_content=self._read_text(path),
            metadata=metadata,
        )

    def _load_pdf(self, path: Path) -> list[Document]:
        """逐页加载 PDF，并保留页码元数据。"""

        try:
            reader = PdfReader(str(path))
        except (OSError, PdfReadError) as exc:
            raise KnowledgeBaseError(f"Could not read PDF {path}: {exc}") from exc

        if reader.is_encrypted:
            try:
                password_result = reader.decrypt("")
            except Exception as exc:
                raise KnowledgeBaseError(
                    f"Encrypted PDF requires a password: {path}"
                ) from exc
            if password_result == 0:
                raise KnowledgeBaseError(f"Encrypted PDF requires a password: {path}")

        documents: list[Document] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            metadata = self._base_metadata(path)
            metadata["page"] = page_number
            documents.append(Document(page_content=text, metadata=metadata))
        return documents

    def _base_metadata(self, path: Path) -> dict[str, str | int]:
        """生成各类文档共用的稳定元数据。"""

        resolved = path.resolve()
        document_id = hashlib.sha256(
            resolved.as_posix().casefold().encode("utf-8")
        ).hexdigest()
        return {
            "document_id": document_id,
            "source": resolved.name,
            "source_path": str(resolved),
            "file_type": resolved.suffix.casefold().lstrip("."),
        }

    def _read_text(self, path: Path) -> str:
        """优先按 UTF-8 读取文本，失败后兼容 GB18030。"""

        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                return path.read_text(encoding="gb18030")
            except UnicodeDecodeError as exc:
                raise KnowledgeBaseError(
                    f"Could not decode text file as UTF-8 or GB18030: {path}"
                ) from exc
