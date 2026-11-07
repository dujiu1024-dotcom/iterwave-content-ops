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


def test_reference_image_bytes_are_sent_to_provider_edit_endpoint(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.core import media_generation as media

    reference_bytes = b"verified-reference-image-bytes"
    captured = {}

    class _Response:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"data": [{"b64_json": "ignored-by-decoder"}]}

    class _HttpClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def bounded_post(_client, url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(media.httpx, "Client", lambda **_kwargs: _HttpClient())
    monkeypatch.setattr(media, "_bounded_post", bounded_post)
    monkeypatch.setattr(
        media, "_decode_payload",
        lambda payload, cfg: (b"generated", "image/png", ".png", 256, 384),
    )
    cfg = SimpleNamespace(media_generation=SimpleNamespace(
        timeout_seconds=10, connect_timeout_seconds=2, max_response_bytes=1024,
        watermark=None,
    ))
    endpoint = media._ImageEndpoint(
        label="primary", provider="openai", base_url="https://images.example/v1",
        model="image-model", api_key="test-key",
    )

    result = media._request_endpoint(
        endpoint, "edit this reference", "1024x1536", cfg=cfg,
        source_path=None, source_mime_type="image/png",
        source_filename="approved.png", source_bytes=reference_bytes,
    )

    assert result[0] == b"generated"
    assert captured["url"].endswith("/images/edits")
    assert captured["files"]["image"] == ("approved.png", reference_bytes, "image/png")
