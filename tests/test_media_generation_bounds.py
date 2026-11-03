import httpx
import pytest

from src.core.media_generation import ImageGenerationError, _bounded_post


class _StreamedResponse:
    status_code = 200
    request = httpx.Request("POST", "https://image.example/generate")

    def __init__(self, chunks, content_length=None):
        self._chunks = chunks
        self.headers = {"content-length": str(content_length)} if content_length is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_bytes(self):
        yield from self._chunks


class _Client:
    def __init__(self, response):
        self.response = response

    def stream(self, *_args, **_kwargs):
        return self.response


def test_bounded_post_rejects_large_declared_content_length():
    client = _Client(_StreamedResponse([b"small"], content_length=100))
    with pytest.raises(ImageGenerationError, match="超过允许大小"):
        _bounded_post(client, "https://image.example/generate", max_bytes=10)


def test_bounded_post_stops_when_streamed_body_exceeds_limit():
    client = _Client(_StreamedResponse([b"12345", b"67890"]))
    with pytest.raises(ImageGenerationError, match="超过允许大小"):
        _bounded_post(client, "https://image.example/generate", max_bytes=8)


def test_bounded_post_returns_bounded_response():
    client = _Client(_StreamedResponse([b"123", b"456"]))
    response = _bounded_post(client, "https://image.example/generate", max_bytes=8)
    assert response.status_code == 200
    assert response.content == b"123456"
