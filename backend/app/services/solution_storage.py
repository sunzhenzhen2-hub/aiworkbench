"""方案库本地文件存储：类型校验、安全落盘、删除。"""

from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import settings

# 扩展名 -> MIME（优先于猜测）
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def upload_root() -> Path:
    root = Path(settings.solution_upload_dir)
    if not root.is_absolute():
        # 相对路径相对 backend 工作目录（通常 PYTHONPATH=. 时 cwd=backend）
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def is_image_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def safe_original_name(filename: str | None) -> str:
    name = (filename or "unnamed").strip() or "unnamed"
    name = Path(name).name  # 去掉路径穿越
    name = re.sub(r"[\x00-\x1f]", "", name)
    if len(name) > 200:
        stem, suffix = Path(name).stem[:180], Path(name).suffix
        name = stem + suffix
    return name


async def save_upload(file: UploadFile) -> tuple[str, str, str, int]:
    """
    保存上传文件。
    返回: (original_filename, stored_relative_path, mime_type, file_size)
    stored_relative_path 相对 upload_root，形如 {uuid}{ext}
    """
    original = safe_original_name(file.filename)
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="仅支持 PDF、Word(.docx)、PPT(.pptx) 与常见图片格式",
        )

    data = await file.read()
    size = len(data)
    max_bytes = settings.solution_max_upload_bytes
    if size <= 0:
        raise HTTPException(status_code=400, detail="文件为空")
    if size > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"单文件不能超过 {mb}MB")

    mime = ALLOWED_EXTENSIONS[ext]
    if file.content_type and file.content_type.startswith("image/") and ext in IMAGE_EXTENSIONS:
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
