"""
Device Token Authentication for Hivemind.

Flow:
1. On server start, an 8-character alphanumeric access code is generated and printed to terminal.
2. Unauthenticated devices see a login screen and must enter the access code.
3. If HIVEMIND_PASSWORD is set in .env, the user must ALSO enter the password.
4. On correct code (+password), the device receives a permanent device_token (stored in browser).
5. The device_token is sent with every request (cookie + header fallback).
6. Approved devices never need to enter the code again.
7. The access code rotates every 5 minutes (old codes are rejected).
8. After 3 failed attempts from an IP, that IP is blocked for 30 minutes.

Storage: SQLite (same DB as the rest of the app).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

ACCESS_CODE_LENGTH = 8
ACCESS_CODE_ROTATION_SECONDS = 300  # 5 minutes
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_SECONDS = 1800  # 30 minutes
DEVICE_TOKEN_BYTES = 32  # 256-bit token
COOKIE_NAME = "hivemind_device_token"
COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year


@dataclass
class DeviceInfo:
    """Represents an approved device."""

    device_id: str
    token_hash: str
    name: str
    ip_address: str
    user_agent: str
    created_at: float
    last_seen: float


@dataclass
class AccessCode:
    """A time-limited access code."""

    code: str
    created_at: float
    expires_at: float


class DeviceAuthManager:
    """Manages device-based authentication.

    Singleton: multiple calls to DeviceAuthManager() return the same instance,
    ensuring the access code generated at startup is the same one validated
    by the API verify endpoint.
    """

    _instance: DeviceAuthManager | None = None

    def __new__(cls, db_path: str | Path = "hivemind_auth.db"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str | Path = "hivemind_auth.db"):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._db_path = str(db_path)
        self._lock = RLock()
        self._current_code: AccessCode | None = None
        self._failed_attempts: dict[str, list[float]] = {}  # ip -> [timestamps]
        self._init_db()
        self._rotate_code()

    # ── Database ─────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approved_devices (
                    device_id   TEXT PRIMARY KEY,
                    token_hash  TEXT NOT NULL,
                    name        TEXT NOT NULL DEFAULT '',
                    ip_address  TEXT NOT NULL DEFAULT '',
                    user_agent  TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL,
                    last_seen   REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_devices_last_seen
                ON approved_devices(last_seen)
            """)
        logger.info("Device auth database initialized at %s", self._db_path)

    # ── Access Code ──────────────────────────────────────────────────────

    # Alphanumeric charset without ambiguous characters (0/O, 1/I/l)
    _CODE_CHARSET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

    def _rotate_code(self) -> str:
        """Generate a new access code."""
        code = "".join(secrets.choice(self._CODE_CHARSET) for _ in range(ACCESS_CODE_LENGTH))
        now = time.time()
        self._current_code = AccessCode(
            code=code,
            created_at=now,
            expires_at=now + ACCESS_CODE_ROTATION_SECONDS,
        )
        return code

    def get_current_code(self) -> str:
        """Get the current access code, rotating if expired."""
        with self._lock:
            if self._current_code is None or time.time() > self._current_code.expires_at:
                self._rotate_code()
            if self._current_code is None:
                raise RuntimeError("Access code generation failed")
            return self._current_code.code

    def force_rotate_code(self) -> str:
        """Force-generate a new access code (e.g., from settings)."""
        with self._lock:
            return self._rotate_code()

    # ── Rate Limiting ────────────────────────────────────────────────────

    def _is_ip_locked(self, ip: str) -> bool:
        """Check if an IP is locked out due to too many failed attempts."""
        now = time.time()
        attempts = self._failed_attempts.get(ip, [])
        # Clean old attempts
        recent = [t for t in attempts if now - t < LOCKOUT_SECONDS]
        self._failed_attempts[ip] = recent
        return len(recent) >= MAX_FAILED_ATTEMPTS

    def _record_failed_attempt(self, ip: str) -> None:
        """Record a failed login attempt."""
        if ip not in self._failed_attempts:
            self._failed_attempts[ip] = []
        self._failed_attempts[ip].append(time.time())

    def _clear_failed_attempts(self, ip: str) -> None:
        """Clear failed attempts for an IP after successful auth."""
        self._failed_attempts.pop(ip, None)

    # ── Token Hashing ────────────────────────────────────────────────────

    @staticmethod
    def _hash_token(token: str) -> str:
        """Hash a device token for storage (SHA-256)."""
        return hashlib.sha256(token.encode()).hexdigest()

    # ── Device Management ────────────────────────────────────────────────

    def verify_access_code(
        self, code: str, ip: str, user_agent: str, password: str = ""
    ) -> str | None:
        """
        Verify an access code (and optional password) and create a new device token.

        Returns the device_token on success, None on failure.
        """
        with self._lock:
            # Check rate limit
            if self._is_ip_locked(ip):
                logger.warning("IP %s is locked out (too many failed attempts)", ip)
                return None

            # Check password if configured
            required_password = os.getenv("HIVEMIND_PASSWORD", "")
            if required_password:
                if not password or not hmac.compare_digest(password, required_password):
                    self._record_failed_attempt(ip)
                    logger.warning("Invalid password from %s", ip)
                    return None

            # Check code (case-insensitive for usability)
            current = self.get_current_code()
            if not hmac.compare_digest(code.strip().upper(), current):
                self._record_failed_attempt(ip)
                remaining = MAX_FAILED_ATTEMPTS - len(
                    [
                        t
                        for t in self._failed_attempts.get(ip, [])
                        if time.time() - t < LOCKOUT_SECONDS
                    ]
                )
                logger.warning(
                    "Invalid access code from %s (%d attempts remaining)", ip, max(0, remaining)
                )
                return None

            # Code is valid — create device token
            self._clear_failed_attempts(ip)
            device_token = secrets.token_urlsafe(DEVICE_TOKEN_BYTES)
            device_id = secrets.token_hex(8)
            token_hash = self._hash_token(device_token)
            now = time.time()

            # Determine device name from user agent
            device_name = self._parse_device_name(user_agent)

            with self._get_conn() as conn:
                # Remove stale entries for the same device (same IP + user agent)
                # to prevent duplicate device entries when IP/session changes
                conn.execute(
                    "DELETE FROM approved_devices WHERE ip_address = ? AND user_agent = ?",
                    (ip, user_agent),
                )
                conn.execute(
                    """INSERT INTO approved_devices
                       (device_id, token_hash, name, ip_address, user_agent, created_at, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (device_id, token_hash, device_name, ip, user_agent, now, now),
                )

            # The access code stays valid until it expires (5-min window).
            # This allows multiple devices to connect with the same code
            # (e.g. laptop + phone) without needing to check the terminal again.

            logger.info(
                "New device approved: %s (%s) from %s",
                device_name,
                device_id,
                ip,
            )
            return device_token

    def verify_device_token(self, token: str) -> bool:
        """Verify a device token is valid (approved device)."""
        if not token:
            return False
        token_hash = self._hash_token(token)
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT device_id FROM approved_devices WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row:
                # Update last_seen
                conn.execute(
                    "UPDATE approved_devices SET last_seen = ? WHERE token_hash = ?",
                    (time.time(), token_hash),
                )
                return True
        return False

    def list_devices(self) -> list[dict]:
        """List all approved devices."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT device_id, name, ip_address, user_agent, created_at, last_seen "
                "FROM approved_devices ORDER BY last_seen DESC"
            ).fetchall()
        return [
            {
                "device_id": r["device_id"],
                "name": r["name"],
                "ip": r["ip_address"],
                "user_agent": r["user_agent"],
                "approved_at": r["created_at"],
                "last_seen": r["last_seen"],
            }
            for r in rows
        ]

    def revoke_device(self, device_id: str) -> bool:
        """Revoke (remove) an approved device."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM approved_devices WHERE device_id = ?",
                (device_id,),
            )
            if cursor.rowcount > 0:
                logger.info("Device revoked: %s", device_id)
                return True
        return False

    def revoke_all_devices(self) -> int:
        """Revoke all approved devices."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM approved_devices")
            count = cursor.rowcount
        logger.info("All devices revoked (%d devices)", count)
        return count

    def device_count(self) -> int:
        """Return the number of approved devices."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM approved_devices").fetchone()
            return row["cnt"] if row else 0

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_device_name(user_agent: str) -> str:
        """Extract a human-readable device name from User-Agent."""
        ua = user_agent.lower()
        if "iphone" in ua:
            return "iPhone"
        if "ipad" in ua:
            return "iPad"
        if "android" in ua:
            if "mobile" in ua:
                return "Android Phone"
            return "Android Tablet"
        if "macintosh" in ua or "mac os" in ua:
            return "Mac"
        if "windows" in ua:
            return "Windows PC"
        if "linux" in ua:
            return "Linux"
        if "chromeos" in ua:
            return "Chromebook"
        return "Unknown Device"

    def print_access_code(self) -> None:
        """Print the current access code to the terminal in a visible way."""
        code = self.get_current_code()
        has_password = bool(os.getenv("HIVEMIND_PASSWORD", ""))
        print(flush=True)
        print("=" * 50)
        print()
        print(f"  ACCESS CODE:  {code}")
        print()
        print("  Enter this code in the browser to connect.")
        print(f"  The code changes every {ACCESS_CODE_ROTATION_SECONDS // 60} minutes.")
        if has_password:
            print("  Password protection is ON.")
        else:
            print("  TIP: Set HIVEMIND_PASSWORD in .env for extra security.")
        print()
        print("=" * 50)
        print(flush=True)
