from types import SimpleNamespace

from src.core.auth import client_ip


def _request(peer: str, forwarded: str = ""):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer),
        headers={"x-forwarded-for": forwarded},
    )


def test_forwarded_ip_is_used_only_for_trusted_proxy(monkeypatch):
    monkeypatch.setenv("ITERWAVE_TRUSTED_PROXY_IPS", "127.0.0.1,::1")
    assert client_ip(_request("127.0.0.1", "203.0.113.9")) == "203.0.113.9"
    # An untrusted direct peer cannot spoof the rate-limit key.
    assert client_ip(_request("198.51.100.7", "203.0.113.9")) == "198.51.100.7"


def test_proxy_chain_walks_from_the_trusted_edge(monkeypatch):
    monkeypatch.setenv("ITERWAVE_TRUSTED_PROXY_IPS", "127.0.0.1,10.0.0.0/8")
    request = _request("10.0.0.2", "198.51.100.8, 10.0.0.1")
    assert client_ip(request) == "198.51.100.8"


def test_invalid_forwarded_chain_is_not_trusted(monkeypatch):
    monkeypatch.setenv("ITERWAVE_TRUSTED_PROXY_IPS", "127.0.0.1")
    assert client_ip(_request("127.0.0.1", "not-an-ip")) == "127.0.0.1"
