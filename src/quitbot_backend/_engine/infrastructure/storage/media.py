"""Account-local copies of resources obtained through QQ's visible UI."""

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_CACHE_BYTES = 512 * 1024 * 1024


def image_extension(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    raise ValueError("QQ 复制的资源不是支持的图片格式")


class ImageCache:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    @staticmethod
    def valid_name(name):
        return bool(re.fullmatch(r"[0-9a-f]{64}\.(?:png|gif|jpg|webp|bmp)", name))

    def put(self, data):
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("图片大小超出 32 MiB 缓存限制")
        name = hashlib.sha256(data).hexdigest() + image_extension(data)
        path = self.directory / name
        if path.is_symlink():
            raise ValueError("图片缓存路径异常")
        self._prune(len(data), keep=name)
        if not path.exists():
            fd, temporary = tempfile.mkstemp(prefix=".image-", dir=self.directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        os.utime(path, None)
        return name

    def get(self, name):
        if not self.valid_name(name):
            raise ValueError("图片标识无效，请使用消息段中的 file 字段")
        path = self.directory / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve().parent != self.directory
        ):
            raise ValueError("当前账号的图片缓存不存在或已清理，请重新读取原消息")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("图片缓存大小异常")
        os.utime(path, None)
        return path

    def _prune(self, incoming, *, keep):
        files = [
            p
            for p in self.directory.iterdir()
            if self.valid_name(p.name) and p.is_file() and not p.is_symlink()
        ]
        total = sum(p.stat().st_size for p in files) + incoming
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if path.name == keep:
                continue
            info = path.stat()
            if total <= MAX_CACHE_BYTES and time.time() - info.st_mtime < 7 * 86400:
                continue
            path.unlink()
            total -= info.st_size
