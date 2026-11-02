"""访问保护 — 访问口令 + 会话 Cookie + 脚本令牌（P0 安全）。

口径：
- 未设置口令：仅允许读取认证状态和设置首次口令；其它业务 API 返回 setup_required
- 本机直连仅在显式设置 ITERWAVE_ALLOW_LOCAL_BOOTSTRAP=1 时可免引导设置；其它首次设置必须提供服务端 ITERWAVE_BOOTSTRAP_TOKEN
- 设置口令后：``/api/*`` 全部校验；页面未登录时只能看到登录页
- 脚本调用：请求头 ``X-Access-Token: <口令>`` 或 ``Authorization: Bearer <口令>``
- 登录和脚本令牌失败共用按 IP 限速（10 分钟内最多 5 次，超出 429）
- 口令以 PBKDF2-HMAC-SHA256 哈希存 config/.env（chmod 600），永不回传明文
- 会话为签名令牌（ITERWAVE_SESSION_SECRET + 口令哈希），重启不掉线，多 worker 可互认
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import sqlite3
import time
from typing import Optional

from .env_store import read_env, write_env

logger = logging.getLogger(__name__)

PASSWORD_ENV = "ACCESS_PASSWORD_HASH"
COOKIE_NAME = "iterwave_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 10 * 60


def client_ip(request) -> str:
    """Resolve the client address without trusting arbitrary forwarded headers.

    Only peers listed in ITERWAVE_TRUSTED_PROXY_IPS may supply X-Forwarded-For.
    The default trusts loopback proxies (common for same-host Nginx deployments).
    """
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return "unknown"

    configured = os.getenv("ITERWAVE_TRUSTED_PROXY_IPS", "127.0.0.1,::1")
    trusted = []
    for value in configured.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            trusted.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            logger.warning("忽略无效的可信代理网段配置")
    if not any(peer_ip in network for network in trusted):
        return str(peer_ip)

    forwarded = request.headers.get("x-forwarded-for", "")
    chain = []
    for value in forwarded.split(","):
        value = value.strip()
        try:
            chain.append(ipaddress.ip_address(value))
        except ValueError:
            # 任一代理链条目无效时不信任整个转发头。
            return str(peer_ip)

    current = peer_ip
    for candidate in reversed(chain):
        if not any(current in network for network in trusted):
            break
        current = candidate
    return str(current)


def is_trusted_local_setup(request) -> bool:
    """仅限直连本机开发环境的首次设置旁路。

    该开关不能安全地用于反向代理/公网部署：代理后端可能表现为 loopback，
    而 Host 由请求方控制。生产或代理部署应使用 ITERWAVE_BOOTSTRAP_TOKEN。
    """
    if os.getenv("ITERWAVE_ALLOW_LOCAL_BOOTSTRAP", "").strip().lower() not in ("1", "true", "yes"):
        return False
    if any(request.headers.get(name) for name in (
        "forwarded", "x-forwarded-for", "x-real-ip", "x-forwarded-proto", "x-forwarded-host"
    )):
        return False
    peer = request.client.host if request.client else ""
    host = (request.url.hostname or "").strip("[]").lower()
    try:
        peer_local = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        peer_local = peer.lower() == "localhost"
    try:
        host_local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        host_local = host == "localhost"
    return peer_local and host_local

SESSION_SECRET_ENV = "ITERWAVE_SESSION_SECRET"
REVOCATION_FILE_NAME = ".session_revocations.json"

# 会话为服务端签名的无状态令牌：重启不掉线、多 worker 共用同一密钥即可互认。
# 签名密钥 = ITERWAVE_SESSION_SECRET（首次使用自动生成写入 .env）+ 当前口令哈希，
# 修改或清除口令后旧会话自动全部失效。登出的令牌记入 .env 同目录的吊销表（0600）。
_revoked: dict[str, float] = {}
_revoked_mtime: float = -1.0


# ----------------------------------------------------------------------
# 口令存取
# ----------------------------------------------------------------------

def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 300_000)
    return f"pbkdf2$300000${salt}${digest.hex()}"


def _verify_hash(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2":
        return False
    try:
        rounds_n = int(rounds)
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds_n)
    return hmac.compare_digest(calc.hex(), digest)


def password_configured() -> bool:
    return bool(read_env(PASSWORD_ENV))


def get_password_hash() -> Optional[str]:
    return read_env(PASSWORD_ENV)


_COMMON_WEAK_PASSWORDS = {
    "12345678", "123456789", "1234567890", "87654321", "password",
    "password1", "password123", "qwerty123", "qwertyui", "admin123",
    "letmein123", "abc12345", "11111111", "00000000",
}


def validate_password_strength(password: str) -> None:
    """公网访问口令：至少 8 位，至少覆盖三类字符，并拒绝常见弱口令。"""
    value = (password or "").strip()
    if len(value) < 8:
        raise ValueError("访问口令至少 8 个字符")
    if len(value) > 64:
        raise ValueError("访问口令最长 64 个字符")
    lowered = value.casefold()
    if lowered in _COMMON_WEAK_PASSWORDS or lowered.isdigit() or lowered.isalpha():
        raise ValueError("访问口令过于简单，请勿使用常见口令或纯数字、纯字母")
    classes = sum((
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    ))
    if classes < 3:
        raise ValueError("访问口令需混合至少三类字符：大小写字母、数字、符号")


def set_password(password: str) -> None:
    password = (password or "").strip()
    validate_password_strength(password)
    write_env(PASSWORD_ENV, _hash_password(password))
    _reset_revocations()
    logger.info("访问口令已更新，已有会话全部失效")


def clear_password() -> None:
    write_env(PASSWORD_ENV, "")
    _reset_revocations()
    logger.info("访问口令已清除，全站恢复免登录（会显示安全警告）")


def verify_password(password: str) -> bool:
    stored = get_password_hash()
    if not stored:
        return False
    return _verify_hash(password or "", stored)


# ----------------------------------------------------------------------
# 会话
# ----------------------------------------------------------------------

def _session_secret() -> bytes:
    secret = read_env(SESSION_SECRET_ENV)
    if not secret:
        secret = secrets.token_urlsafe(48)
        write_env(SESSION_SECRET_ENV, secret)
    return secret.encode("utf-8")


def _signing_key() -> Optional[bytes]:
    stored = get_password_hash()
    if not stored:
        return None
    return hashlib.sha256(_session_secret() + b"|" + stored.encode("utf-8")).digest()


def _sign(payload: str, key: bytes) -> str:
    mac = hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def _revocation_path():
    from .env_store import env_file
    return env_file().parent / REVOCATION_FILE_NAME


def _load_revocations() -> dict[str, float]:
    global _revoked, _revoked_mtime
    path = _revocation_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _revoked
    if mtime != _revoked_mtime:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _revoked = {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            _revoked = {}
        _revoked_mtime = mtime
    return _revoked


def _save_revocations() -> None:
    global _revoked_mtime
    path = _revocation_path()
    now = time.time()
    data = {k: v for k, v in _revoked.items() if v > now}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
        _revoked_mtime = path.stat().st_mtime
    except OSError:
        logger.warning("会话吊销表写入失败，登出仅在当前进程生效")


def _reset_revocations() -> None:
    global _revoked_mtime
    _revoked.clear()
    _revoked_mtime = -1.0
    try:
        _revocation_path().unlink(missing_ok=True)
    except OSError:
        pass


def _parse_session(token: Optional[str]) -> Optional[tuple[str, float]]:
    """校验签名与有效期，返回 (nonce, 过期时间)；无效返回 None。"""
    if not token or len(token) > 256 or token.count(".") != 2:
        return None
    key = _signing_key()
    if key is None:
        return None
    exp_s, nonce, sig = token.split(".")
    if not hmac.compare_digest(_sign(f"{exp_s}.{nonce}", key), sig):
        return None
    try:
        expire = float(int(exp_s))
    except ValueError:
        return None
    if expire < time.time():
        return None
    return nonce, expire


def _new_session() -> str:
    key = _signing_key()
    if key is None:
        raise ValueError("尚未设置访问口令")
    payload = f"{int(time.time() + SESSION_TTL_SECONDS)}.{secrets.token_urlsafe(18)}"
    return f"{payload}.{_sign(payload, key)}"


def _session_valid(token: Optional[str]) -> bool:
    parsed = _parse_session(token)
    if parsed is None:
        return False
    return parsed[0] not in _load_revocations()


def logout_session(token: Optional[str]) -> None:
    parsed = _parse_session(token)
    if parsed is None:
        return
    _load_revocations()
    _revoked[parsed[0]] = parsed[1]
    _save_revocations()


def audit_actor(request) -> str:
    """Return a non-secret, per-session audit principal; never persist a credential."""
    cookie_token = request.cookies.get(COOKIE_NAME)
    parsed = _parse_session(cookie_token)
    if parsed is not None and parsed[0] not in _load_revocations():
        fingerprint = hashlib.sha256(parsed[0].encode("utf-8")).hexdigest()[:20]
        return f"session:{fingerprint}"
    headers = request.headers
    candidate = request_token(None, headers)
    if candidate and verify_password(candidate):
        # Script callers currently share one access token; this is not a human identity.
        return "shared-api-token"
    return "authenticated-unknown"


# ----------------------------------------------------------------------
# 登录限速
# ----------------------------------------------------------------------

def _rate_limit_connection() -> sqlite3.Connection:
    """Open the shared, permission-restricted rate-limit store.

    The store lives beside the configured .env file so all API worker processes
    on this host use the same failure history without depending on process memory.
    """
    from .env_store import env_file

    path = env_file().with_name(".auth_rate_limits.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS auth_failures ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL, failed_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_failures_ip_time "
        "ON auth_failures(ip, failed_at)"
    )
    try:
        path.chmod(0o600)
    except OSError:
        logger.warning("无法收紧认证限流数据库文件权限")
    return conn


def _recent_failures(ip: str) -> list[float]:
    """Return recent failures from the shared store (visible across processes)."""
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    conn = _rate_limit_connection()
    try:
        rows = conn.execute(
            "SELECT failed_at FROM auth_failures WHERE ip=? AND failed_at>=? ORDER BY failed_at",
            (ip, cutoff),
        ).fetchall()
        return [float(row[0]) for row in rows]
    finally:
        conn.close()


def is_rate_limited(ip: str) -> bool:
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    conn = _rate_limit_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM auth_failures WHERE ip=? AND failed_at>=?",
            (ip, cutoff),
        ).fetchone()
        return bool(row and int(row[0]) >= LOGIN_MAX_FAILURES)
    finally:
        conn.close()


def reserve_auth_attempt(ip: str) -> bool:
    """Atomically reserve one authentication attempt before expensive PBKDF2.

    BEGIN IMMEDIATE serializes reservations across threads and processes. This
    prevents concurrent requests from all passing the threshold check before
    any of them records a failed password.
    """
    now = time.time()
    cutoff = now - LOGIN_WINDOW_SECONDS
    conn = _rate_limit_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM auth_failures WHERE failed_at<?", (cutoff,))
        row = conn.execute(
            "SELECT COUNT(*) FROM auth_failures WHERE ip=? AND failed_at>=?",
            (ip, cutoff),
        ).fetchone()
        if row and int(row[0]) >= LOGIN_MAX_FAILURES:
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO auth_failures(ip, failed_at) VALUES (?, ?)",
            (ip, now),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_failure(ip: str) -> None:
    """Persist a failed attempt atomically for every API process on this host."""
    now = time.time()
    cutoff = now - LOGIN_WINDOW_SECONDS
    conn = _rate_limit_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM auth_failures WHERE failed_at<?", (cutoff,))
        conn.execute(
            "INSERT INTO auth_failures(ip, failed_at) VALUES (?, ?)",
            (ip, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def clear_failures(ip: str) -> None:
    conn = _rate_limit_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM auth_failures WHERE ip=?", (ip,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def login(password: str, ip: str) -> str:
    """校验口令并签发会话 token；达到失败阈值后先限速，再执行昂贵的 PBKDF2。"""
    # 限速必须发生在口令校验之前，否则达到阈值后仍可用正确口令登录，
    # 同时攻击者仍可持续触发 PBKDF2 消耗 CPU。
    if not reserve_auth_attempt(ip):
        raise PermissionError("尝试次数过多，请 10 分钟后再试")
    if verify_password(password):
        clear_failures(ip)
        return _new_session()
    raise ValueError("访问口令不正确")


# ----------------------------------------------------------------------
# 请求判定
# ----------------------------------------------------------------------

def request_token(cookie_token: Optional[str], headers) -> Optional[str]:
    """从 Cookie / X-Access-Token / Authorization 头提取候选令牌。"""
    header_token = headers.get("x-access-token") or ""
    if not header_token:
        auth = headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            header_token = auth[7:].strip()
    return cookie_token or header_token or None


def authenticate_request(cookie_token: Optional[str], headers, ip: Optional[str] = None) -> tuple[bool, bool]:
    """Return (authenticated, rate_limited) while preserving the exact attempt boundary."""
    if not password_configured():
        return False, False
    # Cookie 只接受服务端签发的会话 token；无效 Cookie 不触发 PBKDF2。
    if _session_valid(cookie_token):
        return True, False

    candidate = request_token(None, headers)
    if not candidate:
        return False, False
    client_ip = ip or "unknown"
    if not reserve_auth_attempt(client_ip):
        return False, True
    if verify_password(candidate):
        clear_failures(client_ip)
        return True, False
    # 第五次错误仍返回认证失败；只有后续被拒绝的请求返回 429。
    return False, False


def is_authenticated(cookie_token: Optional[str], headers, ip: Optional[str] = None) -> bool:
    """Boolean compatibility wrapper for callers that do not need the rate-limit reason."""
    authenticated, _rate_limited = authenticate_request(cookie_token, headers, ip=ip)
    return authenticated


def status(cookie_token: Optional[str], headers, ip: Optional[str] = None) -> dict:
    configured = password_configured()
    authenticated = is_authenticated(cookie_token, headers, ip=ip)
    return {
        "configured": configured,
        "authenticated": authenticated,
        "needs_login": configured and not authenticated,
        "cookie_name": COOKIE_NAME,
    }


def session_cookie_header(token: str, *, secure: bool = False) -> str:
    """生成会话 Cookie；HTTPS 部署附加 Secure，纯 HTTP 本地开发仍可登录。"""
    attrs = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax"
    if secure:
        attrs += "; Secure"
    return f"{attrs}; Max-Age={SESSION_TTL_SECONDS}"


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def mask_password_hash(stored: Optional[str]) -> str:
    """只暴露前缀，方便排查配置，不泄露哈希本体。"""
    if not stored:
        return ""
    return stored[:10] + "***"


def basic_token_preview(password: str) -> str:
    """脚本用法提示里的占位，不返回真实口令。"""
    return base64.b64encode(password.encode()).decode()[:6] + "***"
