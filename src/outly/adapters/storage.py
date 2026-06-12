import asyncio
import time
from pathlib import Path

import httpx


class LocalFileStorage:
    def __init__(self, directory: str, base_url: str):
        self._directory = Path(directory)
        self._base_url = base_url.rstrip("/")
        self._directory.mkdir(parents=True, exist_ok=True)

    def _path_for_url(self, url: str) -> Path | None:
        prefix = f"{self._base_url}/files/"
        if not url.startswith(prefix):
            return None
        name = url.removeprefix(prefix)
        path = (self._directory / name).resolve()
        if not path.is_relative_to(self._directory.resolve()):
            return None
        return path

    async def save(self, filename: str, content: bytes) -> str:
        safe_name = filename.replace("/", "_").replace("\\", "_")
        stored_name = f"{int(time.time() * 1000)}-{safe_name}"
        path = self._directory / stored_name
        await asyncio.to_thread(path.write_bytes, content)
        return f"{self._base_url}/files/{stored_name}"

    async def read(self, url: str) -> bytes:
        path = self._path_for_url(url)
        if path is not None:
            return await asyncio.to_thread(path.read_bytes)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def delete(self, url: str) -> None:
        path = self._path_for_url(url)
        if path is None:
            raise ValueError("Invalid attachment URL")
        await asyncio.to_thread(path.unlink, True)
