"""周报/日报证明材料本地存储。"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.services.task_attachment_storage import ALLOWED_EXTENSIONS, safe_original_name


def upload_root() -> Path:
    root = Path(settings.report_attachment_upload_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


async def save_upload(file: UploadFile) -> tuple[str, str, str, int]:
    """返回: (original_filename, stored_relative_path, mime_type, file_size)。"""
    original = safe_original_name(file.filename)
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="仅支持 PDF、Office 文档、文本与常见图片格式",
        )

    data = await file.read()
    size = len(data)
    max_bytes = settings.report_attachment_max_upload_bytes
    if size <= 0:
        raise HTTPException(status_code=400, detail="文件为空")
    if size > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"单文件不能超过 {mb}MB")

    mime = ALLOWED_EXTENSIONS[ext]
    if file.content_type and file.content_type.startswith("image/"):
        mime = file.content_type
    elif not mime:
        mime = mimetypes.guess_type(original)[0] or "application/octet-stream"

    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = upload_root() / stored_name
    dest.write_bytes(data)
    return original, stored_name, mime, size


def absolute_path(stored_relative: str) -> Path:
    path = upload_root() / Path(stored_relative).name
    if not path.is_file():
        raise FileNotFoundError(stored_relative)
    return path


def delete_stored(stored_relative: str) -> None:
    try:
        path = absolute_path(stored_relative)
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
