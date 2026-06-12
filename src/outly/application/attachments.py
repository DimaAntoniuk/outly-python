from typing import Any

from .errors import BadRequest, Forbidden
from .ports import AttachmentRepository, FileStorage

ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
        "text/plain",
        "image/png",
        "image/jpeg",
        "image/gif",
    }
)
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_TOTAL_SIZE = 25 * 1024 * 1024
MAX_FILES = 10


class AttachmentService:
    def __init__(self, attachment_repo: AttachmentRepository, storage: FileStorage):
        self._attachment_repo = attachment_repo
        self._storage = storage

    async def upload(self, files: list[tuple[str, str, bytes]]) -> list[dict[str, Any]]:
        if not files:
            raise BadRequest("No files provided")
        if len(files) > MAX_FILES:
            raise BadRequest("Invalid file upload")
        for _, mime_type, content in files:
            if mime_type not in ALLOWED_MIME_TYPES:
                raise BadRequest(
                    f'File type "{mime_type}" is not allowed. '
                    "Allowed: PDF, DOC, DOCX, XLS, XLSX, CSV, TXT, PNG, JPG, GIF"
                )
            if len(content) > MAX_FILE_SIZE:
                raise BadRequest(
                    f"File exceeds the {MAX_FILE_SIZE // (1024 * 1024)} MB size limit"
                )
        total = sum(len(content) for _, _, content in files)
        if total > MAX_TOTAL_SIZE:
            raise BadRequest(
                f"Total upload size exceeds the {MAX_TOTAL_SIZE // (1024 * 1024)} MB limit"
            )
        results = []
        for filename, mime_type, content in files:
            url = await self._storage.save(filename, content)
            results.append(
                {"url": url, "filename": filename, "size": len(content), "mimeType": mime_type}
            )
        return results

    async def delete(self, user_id: str, url: object) -> None:
        if not url or not isinstance(url, str):
            raise BadRequest("URL is required")
        owner_id = await self._attachment_repo.owner_id_by_url(url)
        if owner_id is not None and owner_id != user_id:
            raise Forbidden("Forbidden")
        await self._storage.delete(url)
