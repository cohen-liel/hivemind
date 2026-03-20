"""Authentication endpoints — device token verification, status, and management.

Handles the device-based authentication flow including access code verification,
device listing/revocation, and code rotation.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from dashboard.routers import _get_device_auth, _problem

router = APIRouter(tags=["auth"])


@router.post("/api/auth/verify")
async def verify_access_code(request: Request):
    """Verify an access code and return a device token."""
    from config import DEVICE_AUTH_ENABLED
    from device_auth import COOKIE_NAME

    if not DEVICE_AUTH_ENABLED:
        return _problem(400, "Device authentication is disabled")

    _device_auth = _get_device_auth()

    body = await request.json()

    # --- Legacy API key authentication ---
    # The frontend 'API Key' login mode sends {api_key: "..."}.
    # Verify it against DASHBOARD_API_KEY and issue a device token.
    incoming_api_key = body.get("api_key", "").strip()
    if incoming_api_key:
        import hmac as _hmac

        _raw_api_key = os.getenv("DASHBOARD_API_KEY", "")
        if not _raw_api_key:
            return _problem(400, "API key authentication is not configured. Set DASHBOARD_API_KEY.")
        if not _hmac.compare_digest(incoming_api_key, _raw_api_key):
            return _problem(401, "Invalid API key")

        # API key is valid — use the access code flow internally to issue
        # a proper device token registered in the DB.
        ip = request.client.host if request.client else "unknown"
        ua = request.headers.get("user-agent", "")
        current_code = _device_auth.get_current_code()
        device_token = _device_auth.verify_access_code(current_code, ip, ua)
        if device_token is None:
            return _problem(500, "Failed to issue device token")

        response = JSONResponse(
            {"ok": True, "message": "Authenticated via API key", "device_token": device_token}
        )
        response.set_cookie(
            key=COOKIE_NAME,
            value=device_token,
            max_age=365 * 24 * 3600,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return response

    # --- Device auth: access code + optional password ---
    code = body.get("code", "").strip()
    if not code:
        return _problem(400, "Access code is required")

    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")

    password = body.get("password", "").strip()
    device_token = _device_auth.verify_access_code(code, ip, ua, password=password)
    if device_token is None:
        return _problem(401, "Invalid access code or too many attempts")

    response = JSONResponse(
        {
            "ok": True,
            "message": "Device approved",
            "device_token": device_token,
        }
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=device_token,
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/api/auth/status")
async def auth_status(request: Request):
    """Check if the current device is authenticated."""
    from config import DEVICE_AUTH_ENABLED
    from device_auth import COOKIE_NAME

    if not DEVICE_AUTH_ENABLED:
        return {"authenticated": True, "password_required": False}

    _device_auth = _get_device_auth()

    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        token = request.headers.get("X-Device-Token", "")
    is_authenticated = bool(token and _device_auth.verify_device_token(token))
    return {
        "authenticated": is_authenticated,
        "password_required": bool(os.getenv("HIVEMIND_PASSWORD", "")),
    }


@router.get("/api/auth/devices")
async def list_devices(request: Request):
    """List all approved devices (requires auth)."""
    from config import DEVICE_AUTH_ENABLED

    if not DEVICE_AUTH_ENABLED:
        return {"devices": []}
    return {"devices": _get_device_auth().list_devices()}


@router.delete("/api/auth/devices/{device_id}")
async def revoke_device(device_id: str, request: Request):
    """Revoke an approved device."""
    from config import DEVICE_AUTH_ENABLED

    if not DEVICE_AUTH_ENABLED:
        return _problem(400, "Device authentication is disabled")
    if _get_device_auth().revoke_device(device_id):
        return {"ok": True, "message": "Device revoked"}
    return _problem(404, "Device not found")


@router.post("/api/auth/rotate-code")
async def rotate_code(request: Request):
    """Force-rotate the access code."""
    from config import DEVICE_AUTH_ENABLED

    if not DEVICE_AUTH_ENABLED:
        return _problem(400, "Device authentication is disabled")
    _device_auth = _get_device_auth()
    _device_auth.force_rotate_code()
    _device_auth.print_access_code()
    return {"ok": True, "message": "Access code rotated. Check the terminal."}
