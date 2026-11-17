from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import time
import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp

from .client import BaiduApiError, fetch_baidu_download_url, is_baidu_web_auth_error, resolve_baidu_share
from cloud.models import ShareFile, ShareInfo as ResolvedShare


# ── 内置 Freedeck 百度开放平台应用 ──
FREEDECK_BAIDU_APP_KEY = os.environ.get("DECKHARBOR_BAIDU_APP_KEY", "").strip()
FREEDECK_BAIDU_SECRET_KEY = os.environ.get("DECKHARBOR_BAIDU_SECRET_KEY", "").strip()
FREEDECK_BAIDU_REDIRECT_URI = "http://127.0.0.1:20065/baidu/oauth/callback"

BAIDU_OPENAPI_AUTHORIZE_URL = "https://openapi.baidu.com/oauth/2.0/authorize"
BAIDU_OPENAPI_TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"
BAIDU_OPENAPI_XPAN_FILE_URL = "https://pan.baidu.com/rest/2.0/xpan/file"
BAIDU_OPENAPI_XPAN_MULTIMEDIA_URL = "https://pan.baidu.com/rest/2.0/xpan/multimedia"
BAIDU_OPENAPI_XPAN_NAS_URL = "https://pan.baidu.com/rest/2.0/xpan/nas"
BAIDU_OPENAPI_XPAN_SHARE_URL = "https://pan.baidu.com/rest/2.0/xpan/share"
BAIDU_OPENAPI_USERINFO_URL = "https://openapi.baidu.com/rest/2.0/passport/users/getInfo"
BAIDU_OPENAPI_PCS_SUPERFILE_URL = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"
BAIDU_OPENAPI_CLIENT_REV = "2026-04-03.1"
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=8, sock_connect=8, sock_read=20)
_DEFAULT_UA = "Freedeck/1.0 (SteamDeck; BaiduOpenAPI)"
_DOWNLOAD_UA = "pan.baidu.com"
_SHARE_TRANSFER_ROOT = "/freedeckDown"
_LEGACY_SHARE_TRANSFER_ROOTS = ("/FreedeckTmp",)
_UPLOAD_PART_SIZE = 4 * 1024 * 1024
_CA_CANDIDATE_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
    "/etc/openssl/certs/ca-certificates.crt",
)


class BaiduOpenApiError(RuntimeError):
    """百度网盘官方 OpenAPI 异常。"""

    def __init__(self, message: str, *, diagnostics: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def _mask_secret(raw: str, *, head: int = 4, tail: int = 4) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if len(text) <= head + tail:
        return text[: max(1, min(len(text), head))]
    return f"{text[:head]}…{text[-tail:]}"


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_json_value(sub_value) for key, sub_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    return value


def _sanitize_diagnostics(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    diagnostics = _sanitize_json_value(raw or {})
    sensitive_keys = {
        "access_token",
        "refresh_token",
        "secret_key",
        "client_secret",
        "authorization",
    }
    if not isinstance(diagnostics, dict):
        return {}
    for key, value in list(diagnostics.items()):
        normalized_key = str(key or "").strip().lower()
        if normalized_key in sensitive_keys:
            diagnostics[key] = _mask_secret(str(value or ""))
        elif isinstance(value, dict):
            diagnostics[key] = _sanitize_diagnostics(value)
        elif isinstance(value, list):
            next_items: List[Any] = []
            for item in value:
                if isinstance(item, dict):
                    next_items.append(_sanitize_diagnostics(item))
                else:
                    next_items.append(item)
            diagnostics[key] = next_items
    return diagnostics


def _build_ssl_context() -> ssl.SSLContext:
    candidates: List[str] = []
    env_cert_file = str(os.environ.get("SSL_CERT_FILE", "") or "").strip()
    if env_cert_file:
        candidates.append(env_cert_file)
    candidates.extend(list(_CA_CANDIDATE_FILES))
    try:
        import certifi  # type: ignore

        certifi_path = str(certifi.where() or "").strip()
        if certifi_path:
            candidates.append(certifi_path)
    except Exception:
        pass

    seen = set()
    for raw in candidates:
        path = os.path.realpath(os.path.expanduser(str(raw or "").strip()))
        if not path or path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=path)
            return context
        except Exception:
            continue
    return ssl.create_default_context()


async def _request_json(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    merged_headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        merged_headers.update({str(key): str(value) for key, value in headers.items()})

    async def _request_json_via_urllib_async() -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            request_url = str(url or "").strip()
            if params:
                query = urlencode({str(key): value for key, value in params.items()}, doseq=True)
                joiner = "&" if "?" in request_url else "?"
                request_url = f"{request_url}{joiner}{query}"
            payload_data = None
            if data:
                payload_data = urlencode({str(key): value for key, value in data.items()}, doseq=True).encode("utf-8")
            request = urllib_request.Request(request_url, data=payload_data, method=str(method or "GET").upper())
            for key, value in merged_headers.items():
                request.add_header(str(key), str(value))
            try:
                with urllib_request.urlopen(request, timeout=25, context=_build_ssl_context()) as response:
                    status = int(getattr(response, "status", 200) or 200)
                    text = response.read().decode("utf-8", errors="ignore")
            except urllib_error.HTTPError as exc:
                status = int(getattr(exc, "code", 0) or 0)
                text = exc.read().decode("utf-8", errors="ignore")
                try:
                    payload = json.loads(text or "{}")
                except Exception:
                    payload = {}
                raise BaiduOpenApiError(
                    f"百度网盘官方 API 请求失败（HTTP {status}）",
                    diagnostics={
                        "url": url,
                        "status": status,
                        "params": params or {},
                        "response": payload if isinstance(payload, dict) else {"raw": text[:400]},
                        "transport": "urllib",
                    },
                ) from exc
            try:
                payload = json.loads(text or "{}")
            except Exception:
                payload = {}
            if status >= 400:
                raise BaiduOpenApiError(
                    f"百度网盘官方 API 请求失败（HTTP {status}）",
                    diagnostics={
                        "url": url,
                        "status": status,
                        "params": params or {},
                        "response": payload if isinstance(payload, dict) else {"raw": text[:400]},
                        "transport": "urllib",
                    },
                )
            if not isinstance(payload, dict):
                raise BaiduOpenApiError(
                    "百度网盘官方 API 返回格式异常",
                    diagnostics={
                        "url": url,
                        "params": params or {},
                        "response_preview": text[:400],
                        "transport": "urllib",
                    },
                )
            return payload

        return await asyncio.to_thread(_run)

    try:
        async with aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT) as session:
            async with session.request(
                method.upper(),
                url,
                params=params,
                data=data,
                headers=merged_headers,
                ssl=_build_ssl_context(),
            ) as response:
                text = await response.text()
                try:
                    payload = json.loads(text or "{}")
                except Exception:
                    payload = {}
                if response.status == 405 and "pan.baidu.com" in str(urlparse(url).hostname or "").lower():
                    return await _request_json_via_urllib_async()
                if response.status >= 400:
                    raise BaiduOpenApiError(
                        f"百度网盘官方 API 请求失败（HTTP {response.status}）",
                        diagnostics={
                            "url": url,
                            "status": response.status,
                            "params": params or {},
                            "response": payload if isinstance(payload, dict) else {"raw": text[:400]},
                        },
                    )
                if not isinstance(payload, dict):
                    raise BaiduOpenApiError(
                        "百度网盘官方 API 返回格式异常",
                        diagnostics={
                            "url": url,
                            "params": params or {},
                            "response_preview": text[:400],
                        },
                    )
                return payload
    except BaiduOpenApiError:
        raise
    except Exception as exc:
        raise BaiduOpenApiError(
            f"百度网盘官方 API 请求失败：{exc}",
            diagnostics={"url": url, "params": params or {}, "data_keys": sorted((data or {}).keys())},
        ) from exc


def _raise_api_error(message: str, *, payload: Dict[str, Any], diagnostics: Optional[Dict[str, Any]] = None) -> None:
    errno = payload.get("errno", payload.get("error_code", payload.get("errcode", 0)))
    if int(errno or 0) == 0 and not payload.get("error") and not payload.get("error_msg"):
        return
    merged = dict(diagnostics or {})
    merged["errno"] = errno
    if "error" in payload:
        merged["error"] = payload.get("error")
    if "error_description" in payload:
        merged["error_description"] = payload.get("error_description")
    if "error_msg" in payload:
        merged["error_msg"] = payload.get("error_msg")
    detail = (
        str(payload.get("error_msg", "") or "").strip()
        or str(payload.get("error_description", "") or "").strip()
        or str(payload.get("error", "") or "").strip()
    )
    errno_text = str(errno or "").strip()
    if detail:
        detail_lower = detail.lower()
        if errno_text and f"errno={errno_text}" not in detail_lower and f"errno {errno_text}" not in detail_lower:
            detail = f"{detail}（errno={errno_text}）"
        error_message = f"{message}：{detail}"
    elif errno_text and errno_text != "0":
        error_message = f"{message}（errno={errno_text}）"
    else:
        error_message = message
    raise BaiduOpenApiError(error_message, diagnostics=_sanitize_diagnostics(merged))


def build_baidu_oauth_authorize_url(
    *,
    app_key: str,
    redirect_uri: str = "oob",
    scope: Sequence[str] = ("basic", "netdisk"),
    state: str = "freedeck_baidu_openapi",
) -> str:
    client_id = str(app_key or "").strip()
    if not client_id:
        raise ValueError("App Key 为空")
    scope_value = ",".join(str(item or "").strip() for item in list(scope or []) if str(item or "").strip())
    if not scope_value:
        scope_value = "basic,netdisk"
    return (
        f"{BAIDU_OPENAPI_AUTHORIZE_URL}?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scope_value,
                "state": state,
                "display": "page",
            }
        )
    )


async def exchange_baidu_oauth_code(
    *,
    app_key: str,
    secret_key: str,
    code: str,
    redirect_uri: str = "oob",
) -> Dict[str, Any]:
    payload = await _request_json(
        "POST",
        BAIDU_OPENAPI_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": str(code or "").strip(),
            "client_id": str(app_key or "").strip(),
            "client_secret": str(secret_key or "").strip(),
            "redirect_uri": redirect_uri,
        },
    )
    _raise_api_error("百度网盘官方授权失败", payload=payload)
    expires_in = max(0, int(payload.get("expires_in") or 0))
    return {
        "access_token": str(payload.get("access_token", "") or "").strip(),
        "refresh_token": str(payload.get("refresh_token", "") or "").strip(),
        "expires_in": expires_in,
        "expires_at": int(time.time()) + expires_in if expires_in > 0 else 0,
        "scope": str(payload.get("scope", "") or "").strip(),
        "session_key": str(payload.get("session_key", "") or "").strip(),
        "session_secret": str(payload.get("session_secret", "") or "").strip(),
    }


async def refresh_baidu_oauth_token(
    *,
    app_key: str,
    secret_key: str,
    refresh_token: str,
) -> Dict[str, Any]:
    payload = await _request_json(
        "POST",
        BAIDU_OPENAPI_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": str(refresh_token or "").strip(),
            "client_id": str(app_key or "").strip(),
            "client_secret": str(secret_key or "").strip(),
        },
    )
    _raise_api_error("百度网盘官方授权刷新失败", payload=payload)
    expires_in = max(0, int(payload.get("expires_in") or 0))
    return {
        "access_token": str(payload.get("access_token", "") or "").strip(),
        "refresh_token": str(payload.get("refresh_token", "") or refresh_token or "").strip(),
        "expires_in": expires_in,
        "expires_at": int(time.time()) + expires_in if expires_in > 0 else 0,
        "scope": str(payload.get("scope", "") or "").strip(),
    }


async def fetch_baidu_openapi_user_info(*, access_token: str) -> Dict[str, Any]:
    attempts = (
        (
            BAIDU_OPENAPI_XPAN_NAS_URL,
            {"method": "uinfo", "access_token": str(access_token or "").strip()},
        ),
        (
            BAIDU_OPENAPI_USERINFO_URL,
            {"access_token": str(access_token or "").strip()},
        ),
    )
    last_error: Optional[BaiduOpenApiError] = None
    for url, params in attempts:
        try:
            payload = await _request_json("GET", url, params=params)
            _raise_api_error("读取百度网盘官方账号信息失败", payload=payload, diagnostics={"url": url})
            user_account = (
                str(payload.get("netdisk_name", "") or "").strip()
                or str(payload.get("baidu_name", "") or "").strip()
                or str(payload.get("uname", "") or "").strip()
                or str(payload.get("display_name", "") or "").strip()
            )
            uid = (
                str(payload.get("uk", "") or "").strip()
                or str(payload.get("userid", "") or "").strip()
                or str(payload.get("uid", "") or "").strip()
            )
            return {
                "user_account": user_account,
                "uid": uid,
                "raw": _sanitize_diagnostics(payload),
            }
        except BaiduOpenApiError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise BaiduOpenApiError("读取百度网盘官方账号信息失败")


async def _list_baidu_openapi_dir(
    *,
    access_token: str,
    path: str,
    folder_only: bool = False,
) -> List[Dict[str, Any]]:
    payload = await _request_json(
        "GET",
        BAIDU_OPENAPI_XPAN_FILE_URL,
        params={
            "method": "list",
            "dir": str(path or "").strip() or "/",
            "folder": 1 if bool(folder_only) else 0,
            "limit": 1000,
            "web": 1,
            "access_token": str(access_token or "").strip(),
        },
    )
    _raise_api_error(
        "读取百度网盘官方目录失败",
        payload=payload,
        diagnostics={"path": str(path or "").strip() or "/"},
    )
    items = payload.get("list") if isinstance(payload.get("list"), list) else []
    return [item for item in items if isinstance(item, dict)]


async def _fetch_baidu_openapi_metas_by_fsids(
    *,
    access_token: str,
    fs_ids: Sequence[str],
    dlink: bool = False,
) -> List[Dict[str, Any]]:
    fsids: List[int] = []
    for raw in list(fs_ids or []):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            fsids.append(int(text))
        except Exception:
            continue
    if not fsids:
        raise BaiduOpenApiError("百度网盘官方资源 fs_id 无效", diagnostics={"fs_ids": list(fs_ids or [])})
    attempts = (
        BAIDU_OPENAPI_XPAN_MULTIMEDIA_URL,
        BAIDU_OPENAPI_XPAN_FILE_URL,
    )
    last_error: Optional[BaiduOpenApiError] = None
    for url in attempts:
        try:
            payload = await _request_json(
                "GET",
                url,
                params={
                    "method": "filemetas",
                    "fsids": json.dumps(fsids, ensure_ascii=False),
                    "dlink": 1 if dlink else 0,
                    "thumb": 0,
                    "extra": 1,
                    "access_token": str(access_token or "").strip(),
                },
            )
            _raise_api_error(
                "读取百度网盘官方文件元数据失败",
                payload=payload,
                diagnostics={"url": url, "fs_ids": fsids, "dlink": bool(dlink)},
            )
            metas = payload.get("list") if isinstance(payload.get("list"), list) else []
            return [item for item in metas if isinstance(item, dict)]
        except BaiduOpenApiError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise BaiduOpenApiError("读取百度网盘官方文件元数据失败")


def _normalize_meta_item(item: Dict[str, Any], *, display_name: str = "") -> Dict[str, Any]:
    fs_id = str(item.get("fs_id", "") or item.get("fsid", "") or "").strip()
    path = str(item.get("path", "") or "").strip()
    server_filename = str(item.get("server_filename", "") or item.get("filename", "") or "").strip()
    name = str(display_name or server_filename or os.path.basename(path.rstrip("/")) or fs_id).strip()
    is_dir = int(item.get("isdir", item.get("is_dir", 0)) or 0) == 1
    if not is_dir:
        try:
            is_dir = int(item.get("fs_id", item.get("fsid", 0)) or 0) == 0 and "dir_empty" in item
        except Exception:
            is_dir = False
    return {
        "file_id": fs_id,
        "name": name,
        "size": max(0, int(item.get("size", 0) or 0)),
        "is_folder": is_dir,
        "path": path,
        "folder_path": str(os.path.dirname(path) or "/").strip() if path else "",
        "dlink": str(item.get("dlink", "") or "").strip(),
        "raw": _sanitize_diagnostics(item),
    }


def _is_better_speed_test_candidate(candidate: Dict[str, Any], current_best: Optional[Dict[str, Any]]) -> bool:
    if current_best is None:
        return True
    candidate_size = max(0, int(candidate.get("size", 0) or 0))
    current_size = max(0, int(current_best.get("size", 0) or 0))
    if candidate_size != current_size:
        return candidate_size > current_size
    candidate_depth = max(0, int(candidate.get("_scan_depth", 0) or 0))
    current_depth = max(0, int(current_best.get("_scan_depth", 0) or 0))
    if candidate_depth != current_depth:
        return candidate_depth < current_depth
    candidate_name = str(candidate.get("name", "") or "").strip().lower()
    current_name = str(current_best.get("name", "") or "").strip().lower()
    if candidate_name != current_name:
        return candidate_name < current_name
    return str(candidate.get("path", "") or "").strip() < str(current_best.get("path", "") or "").strip()


async def pick_baidu_openapi_speed_test_file(
    *,
    access_token: str,
    preferred_paths: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    for preferred_path_raw in list(preferred_paths or []):
        preferred_path = str(preferred_path_raw or "").strip()
        if not preferred_path:
            continue
        if not preferred_path.startswith("/"):
            preferred_path = f"/{preferred_path.lstrip('/')}"
        parent_dir = os.path.dirname(preferred_path.rstrip("/")) or "/"
        target_name = os.path.basename(preferred_path.rstrip("/"))
        if not target_name:
            continue
        try:
            parent_items = await _list_baidu_openapi_dir(access_token=access_token, path=parent_dir)
        except BaiduOpenApiError:
            continue
        matched_item: Optional[Dict[str, Any]] = None
        for item in parent_items:
            normalized = _normalize_meta_item(item)
            if bool(normalized.get("is_folder", False)):
                continue
            current_path = str(normalized.get("path", "") or "").strip()
            current_name = str(normalized.get("name", "") or "").strip()
            if current_path == preferred_path or current_name == target_name:
                matched_item = normalized
                break
        if matched_item is None:
            continue
        if str(matched_item.get("file_id", "") or "").strip() or str(matched_item.get("path", "") or "").strip():
            if not str(matched_item.get("path", "") or "").strip():
                metas = await _fetch_baidu_openapi_metas_by_fsids(
                    access_token=access_token,
                    fs_ids=[str(matched_item.get("file_id", "") or "").strip()],
                    dlink=False,
                )
                if metas:
                    return _normalize_meta_item(metas[0], display_name=str(matched_item.get("name", "") or "").strip())
            return matched_item

    root_items = await _list_baidu_openapi_dir(access_token=access_token, path="/")
    best_root_file: Optional[Dict[str, Any]] = None
    root_dirs: List[str] = []

    for item in root_items:
        normalized = _normalize_meta_item(item)
        if bool(normalized.get("is_folder", False)):
            folder_path = str(normalized.get("path", "") or "").strip()
            if folder_path and folder_path != "/":
                root_dirs.append(folder_path)
            continue
        if not str(normalized.get("file_id", "") or "").strip() and not str(normalized.get("path", "") or "").strip():
            continue
        candidate = dict(normalized)
        candidate["_scan_depth"] = 0
        if _is_better_speed_test_candidate(candidate, best_root_file):
            best_root_file = candidate

    if best_root_file is not None:
        best_root_file.pop("_scan_depth", None)
        if not str(best_root_file.get("path", "") or "").strip():
            metas = await _fetch_baidu_openapi_metas_by_fsids(
                access_token=access_token,
                fs_ids=[str(best_root_file.get("file_id", "") or "").strip()],
                dlink=False,
            )
            if metas:
                return _normalize_meta_item(metas[0], display_name=str(best_root_file.get("name", "") or "").strip())
        return best_root_file

    best_nested_file: Optional[Dict[str, Any]] = None
    pending_dirs: List[Tuple[str, int]] = [(folder_path, 1) for folder_path in root_dirs[:8]]
    queued_dirs = {folder_path for folder_path, _ in pending_dirs}
    scanned_dirs: List[str] = []

    while pending_dirs and len(scanned_dirs) < 12:
        current_dir, depth = pending_dirs.pop(0)
        scanned_dirs.append(current_dir)
        try:
            current_items = await _list_baidu_openapi_dir(access_token=access_token, path=current_dir)
        except BaiduOpenApiError:
            continue

        child_dirs: List[str] = []
        for item in current_items:
            normalized = _normalize_meta_item(item)
            if bool(normalized.get("is_folder", False)):
                child_path = str(normalized.get("path", "") or "").strip()
                if depth < 2 and child_path and child_path not in queued_dirs:
                    child_dirs.append(child_path)
                continue
            if not str(normalized.get("file_id", "") or "").strip() and not str(normalized.get("path", "") or "").strip():
                continue
            candidate = dict(normalized)
            candidate["_scan_depth"] = depth
            if _is_better_speed_test_candidate(candidate, best_nested_file):
                best_nested_file = candidate

        if depth < 2:
            for child_path in child_dirs[:4]:
                pending_dirs.append((child_path, depth + 1))
                queued_dirs.add(child_path)

    if best_nested_file is not None:
        best_nested_file.pop("_scan_depth", None)
        if not str(best_nested_file.get("path", "") or "").strip():
            metas = await _fetch_baidu_openapi_metas_by_fsids(
                access_token=access_token,
                fs_ids=[str(best_nested_file.get("file_id", "") or "").strip()],
                dlink=False,
            )
            if metas:
                return _normalize_meta_item(metas[0], display_name=str(best_nested_file.get("name", "") or "").strip())
        return best_nested_file

    raise BaiduOpenApiError(
        "你的百度网盘里没有找到可测速的文件",
        diagnostics={
            "root_dir_count": len(root_dirs),
            "scanned_dirs": scanned_dirs,
        },
    )


def _safe_transfer_segment(raw: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "_", str(raw or "").strip())
    return normalized.strip("._") or fallback


def _build_openapi_share_transfer_dir(*, share_id: str, file_id: str) -> str:
    share_segment = _safe_transfer_segment(str(share_id or "").strip(), "share")
    file_segment = _safe_transfer_segment(str(file_id or "").strip(), "file")
    return f"{_SHARE_TRANSFER_ROOT}/openapi-share/{share_segment}/{file_segment}"


def _build_openapi_share_transfer_batch_dir(*, share_id: str, file_ids: Sequence[str]) -> str:
    share_segment = _safe_transfer_segment(str(share_id or "").strip(), "share")
    normalized_ids = sorted(str(item or "").strip() for item in list(file_ids or []) if str(item or "").strip())
    batch_key = hashlib.sha1(",".join(normalized_ids).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{_SHARE_TRANSFER_ROOT}/openapi-share/{share_segment}/batch-{batch_key}"


def _build_openapi_share_transfer_dir_candidates(
    *,
    share_id: str,
    file_id: str,
    cached_dir: str = "",
) -> List[str]:
    share_segment = _safe_transfer_segment(str(share_id or "").strip(), "share")
    candidates: List[str] = []
    roots = [_SHARE_TRANSFER_ROOT, *_LEGACY_SHARE_TRANSFER_ROOTS]
    raw_candidates: List[str] = [str(cached_dir or "").strip()]
    for root in roots:
        normalized_root = str(root or "").strip()
        if not normalized_root:
            continue
        raw_candidates.extend(
            [
                f"{normalized_root}/openapi-share/{share_segment}/{_safe_transfer_segment(str(file_id or '').strip(), 'file')}",
                f"{normalized_root}/openapi-share/{share_segment}",
                normalized_root,
            ]
        )
    for raw in raw_candidates:
        candidate = str(raw or "").strip()
        if not candidate or candidate in candidates:
            continue
        candidates.append(candidate)
    return candidates


def _normalize_openapi_dir_path(raw_path: str) -> str:
    path = str(raw_path or "").strip()
    if not path:
        return "/"
    if not path.startswith("/"):
        path = f"/{path.lstrip('/')}"
    normalized = re.sub(r"/{2,}", "/", path).rstrip("/")
    return normalized or "/"


def _iter_openapi_dir_prefixes(raw_path: str) -> List[str]:
    normalized = _normalize_openapi_dir_path(raw_path)
    if normalized == "/":
        return []
    parts = [segment for segment in normalized.split("/") if segment]
    current = ""
    prefixes: List[str] = []
    for part in parts:
        current = f"{current}/{part}"
        prefixes.append(current)
    return prefixes


async def _openapi_dir_exists(*, access_token: str, path: str) -> bool:
    normalized = _normalize_openapi_dir_path(path)
    if normalized == "/":
        return True
    parent_dir = _normalize_openapi_dir_path(os.path.dirname(normalized) or "/")
    target_name = os.path.basename(normalized)
    try:
        items = await _list_baidu_openapi_dir(access_token=access_token, path=parent_dir, folder_only=False)
    except BaiduOpenApiError:
        return False
    for item in items:
        normalized_item = _normalize_meta_item(item)
        if not bool(normalized_item.get("is_folder", False)):
            continue
        item_path = _normalize_openapi_dir_path(str(normalized_item.get("path", "") or "").strip())
        item_name = str(normalized_item.get("name", "") or "").strip()
        if item_path == normalized or item_name == target_name:
            return True
    return False


async def _ensure_openapi_dir_exists(*, access_token: str, path: str) -> None:
    for current_dir in _iter_openapi_dir_prefixes(path):
        if await _openapi_dir_exists(access_token=access_token, path=current_dir):
            continue
        try:
            payload = await _request_json(
                "POST",
                BAIDU_OPENAPI_XPAN_FILE_URL,
                params={
                    "method": "create",
                    "access_token": str(access_token or "").strip(),
                },
                data={
                    "path": current_dir,
                    "isdir": 1,
                    "size": 0,
                },
            )
            _raise_api_error(
                "创建百度网盘官方转存目录失败",
                payload=payload,
                diagnostics={"path": current_dir},
            )
        except BaiduOpenApiError:
            if await _openapi_dir_exists(access_token=access_token, path=current_dir):
                continue
            raise


async def _upload_openapi_tmpfile_part(
    *,
    access_token: str,
    remote_path: str,
    upload_id: str,
    partseq: int,
    chunk: bytes,
) -> Dict[str, Any]:
    params = {
        "method": "upload",
        "type": "tmpfile",
        "access_token": str(access_token or "").strip(),
        "path": str(remote_path or "").strip(),
        "uploadid": str(upload_id or "").strip(),
        "partseq": max(0, int(partseq or 0)),
    }
    headers = {
        "User-Agent": _DOWNLOAD_UA,
        "Referer": "https://pan.baidu.com/disk/home",
        "Accept": "application/json, text/plain, */*",
    }
    form = aiohttp.FormData()
    form.add_field(
        "file",
        chunk,
        filename=f"part-{max(0, int(partseq or 0))}.bin",
        content_type="application/octet-stream",
    )
    try:
        async with aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT) as session:
            async with session.post(
                BAIDU_OPENAPI_PCS_SUPERFILE_URL,
                params=params,
                data=form,
                headers=headers,
                ssl=_build_ssl_context(),
            ) as response:
                text = await response.text()
                try:
                    payload = json.loads(text or "{}")
                except Exception:
                    payload = {}
                if response.status >= 400:
                    raise BaiduOpenApiError(
                        f"百度网盘官方分片上传失败（HTTP {response.status}）",
                        diagnostics={
                            "status": response.status,
                            "remote_path": str(remote_path or "").strip(),
                            "uploadid": str(upload_id or "").strip(),
                            "partseq": max(0, int(partseq or 0)),
                            "response": payload if isinstance(payload, dict) else {"raw": text[:400]},
                        },
                    )
                if not isinstance(payload, dict):
                    raise BaiduOpenApiError(
                        "百度网盘官方分片上传返回格式异常",
                        diagnostics={
                            "remote_path": str(remote_path or "").strip(),
                            "uploadid": str(upload_id or "").strip(),
                            "partseq": max(0, int(partseq or 0)),
                            "response_preview": text[:400],
                        },
                    )
                _raise_api_error(
                    "百度网盘官方分片上传失败",
                    payload=payload,
                    diagnostics={
                        "remote_path": str(remote_path or "").strip(),
                        "uploadid": str(upload_id or "").strip(),
                        "partseq": max(0, int(partseq or 0)),
                    },
                )
                return payload
    except BaiduOpenApiError:
        raise
    except Exception as exc:
        raise BaiduOpenApiError(
            f"百度网盘官方分片上传失败：{exc}",
            diagnostics={
                "remote_path": str(remote_path or "").strip(),
                "uploadid": str(upload_id or "").strip(),
                "partseq": max(0, int(partseq or 0)),
            },
        ) from exc


async def upload_baidu_openapi_file(
    *,
    access_token: str,
    local_file_path: str,
    remote_path: str,
    overwrite: bool = True,
    part_size: int = _UPLOAD_PART_SIZE,
) -> Dict[str, Any]:
    normalized_token = str(access_token or "").strip()
    normalized_local_path = os.path.realpath(os.path.expanduser(str(local_file_path or "").strip()))
    normalized_remote_path = _normalize_openapi_dir_path(remote_path)
    if not normalized_token:
        raise BaiduOpenApiError("百度网盘官方 access_token 为空")
    if not normalized_local_path or not os.path.isfile(normalized_local_path):
        raise BaiduOpenApiError(
            "待上传的本地文件不存在",
            diagnostics={"local_file_path": str(local_file_path or "").strip()},
        )
    if normalized_remote_path in {"", "/"}:
        raise BaiduOpenApiError("百度网盘官方目标路径无效", diagnostics={"remote_path": str(remote_path or "").strip()})

    file_size = max(0, int(os.path.getsize(normalized_local_path) or 0))
    if file_size <= 0:
        raise BaiduOpenApiError(
            "待上传的本地文件为空，无法上传到百度网盘",
            diagnostics={"local_file_path": normalized_local_path},
        )

    parent_dir = _normalize_openapi_dir_path(os.path.dirname(normalized_remote_path) or "/")
    await _ensure_openapi_dir_exists(access_token=normalized_token, path=parent_dir)

    upload_part_size = max(256 * 1024, int(part_size or _UPLOAD_PART_SIZE))
    block_list: List[str] = []
    with open(normalized_local_path, "rb") as fp:
        while True:
            chunk = fp.read(upload_part_size)
            if not chunk:
                break
            block_list.append(hashlib.md5(chunk).hexdigest())

    if not block_list:
        raise BaiduOpenApiError(
            "待上传的本地文件没有可用分片",
            diagnostics={"local_file_path": normalized_local_path, "size": file_size},
        )

    precreate_payload = await _request_json(
        "POST",
        BAIDU_OPENAPI_XPAN_FILE_URL,
        params={
            "method": "precreate",
            "access_token": normalized_token,
        },
        data={
            "path": normalized_remote_path,
            "size": file_size,
            "isdir": 0,
            "autoinit": 1,
            "rtype": 3 if overwrite else 1,
            "block_list": json.dumps(block_list, ensure_ascii=False),
        },
    )
    _raise_api_error(
        "百度网盘官方预创建上传任务失败",
        payload=precreate_payload,
        diagnostics={
            "remote_path": normalized_remote_path,
            "size": file_size,
            "block_count": len(block_list),
        },
    )
    upload_id = str(precreate_payload.get("uploadid", "") or precreate_payload.get("upload_id", "") or "").strip()
    if not upload_id:
        raise BaiduOpenApiError(
            "百度网盘官方预创建未返回 uploadid",
            diagnostics={
                "remote_path": normalized_remote_path,
                "size": file_size,
                "response": _sanitize_diagnostics(precreate_payload),
            },
        )

    uploaded_block_list: List[str] = []
    with open(normalized_local_path, "rb") as fp:
        for partseq, expected_md5 in enumerate(block_list):
            chunk = fp.read(upload_part_size)
            if not chunk:
                raise BaiduOpenApiError(
                    "百度网盘官方上传分片读取失败",
                    diagnostics={
                        "remote_path": normalized_remote_path,
                        "uploadid": upload_id,
                        "partseq": partseq,
                    },
                )
            payload = await _upload_openapi_tmpfile_part(
                access_token=normalized_token,
                remote_path=normalized_remote_path,
                upload_id=upload_id,
                partseq=partseq,
                chunk=chunk,
            )
            uploaded_md5 = str(payload.get("md5", "") or "").strip().lower() or expected_md5.lower()
            if uploaded_md5 != expected_md5.lower():
                raise BaiduOpenApiError(
                    "百度网盘官方分片校验失败",
                    diagnostics={
                        "remote_path": normalized_remote_path,
                        "uploadid": upload_id,
                        "partseq": partseq,
                        "expected_md5": expected_md5.lower(),
                        "uploaded_md5": uploaded_md5,
                    },
                )
            uploaded_block_list.append(uploaded_md5)

    create_payload = await _request_json(
        "POST",
        BAIDU_OPENAPI_XPAN_FILE_URL,
        params={
            "method": "create",
            "access_token": normalized_token,
        },
        data={
            "path": normalized_remote_path,
            "size": file_size,
            "isdir": 0,
            "rtype": 3 if overwrite else 1,
            "uploadid": upload_id,
            "block_list": json.dumps(uploaded_block_list, ensure_ascii=False),
        },
    )
    _raise_api_error(
        "百度网盘官方合并上传文件失败",
        payload=create_payload,
        diagnostics={
            "remote_path": normalized_remote_path,
            "uploadid": upload_id,
            "size": file_size,
            "block_count": len(uploaded_block_list),
        },
    )
    return {
        "fs_id": str(create_payload.get("fs_id", "") or create_payload.get("fsid", "") or "").strip(),
        "path": str(create_payload.get("path", "") or normalized_remote_path).strip() or normalized_remote_path,
        "name": str(create_payload.get("server_filename", "") or os.path.basename(normalized_remote_path)).strip(),
        "size": file_size,
        "uploadid": upload_id,
        "block_count": len(uploaded_block_list),
        "md5": str(create_payload.get("md5", "") or "").strip().lower(),
    }


def _merge_openapi_share_file_meta(existing: Optional[Dict[str, Any]], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing or {})
    for key, value in list(updates.items()):
        if value is None:
            continue
        if isinstance(value, bool):
            merged[key] = value
            continue
        if isinstance(value, str):
            if value.strip():
                merged[key] = value
            continue
        if isinstance(value, (int, float)):
            if int(value) > 0:
                merged[key] = int(value)
            continue
        if isinstance(value, dict) and value:
            merged[key] = dict(value)
    return merged


def _merge_legacy_transfer_cache_into_openapi_ctx(*, share_ctx: Dict[str, Any], file_id: str) -> Optional[Dict[str, Any]]:
    target_id = str(file_id or "").strip()
    if not target_id:
        return None
    ctx_files = share_ctx.get("files") if isinstance(share_ctx.get("files"), dict) else {}
    if not isinstance(ctx_files, dict):
        ctx_files = {}
        share_ctx["files"] = ctx_files
    cached_meta = ctx_files.get(target_id) if isinstance(ctx_files.get(target_id), dict) else {}

    transfer_cache = share_ctx.get("_transfer_cache") if isinstance(share_ctx.get("_transfer_cache"), dict) else {}
    transfer_cache_meta = (
        share_ctx.get("_transfer_cache_meta") if isinstance(share_ctx.get("_transfer_cache_meta"), dict) else {}
    )
    transfer_fs_id = str(transfer_cache.get(target_id, "") or "").strip()
    legacy_meta = transfer_cache_meta.get(target_id) if isinstance(transfer_cache_meta.get(target_id), dict) else {}
    transfer_path = str(legacy_meta.get("to_path", "") or "").strip()
    transfer_dir = str(os.path.dirname(transfer_path) or "/").strip() if transfer_path else ""
    if not transfer_fs_id and not transfer_path:
        return None

    merged_meta = _merge_openapi_share_file_meta(
        cached_meta,
        {
            "file_id": target_id,
            "transfer_fs_id": transfer_fs_id,
            "transfer_path": transfer_path,
            "transfer_folder_path": transfer_dir or str(cached_meta.get("transfer_folder_path", "") or "").strip(),
            "transfer_dir": transfer_dir or str(cached_meta.get("transfer_dir", "") or "").strip(),
        },
    )
    ctx_files[target_id] = merged_meta
    return merged_meta


def _wrap_legacy_baidu_error(exc: BaiduApiError, *, phase: str) -> BaiduOpenApiError:
    diagnostics = _sanitize_diagnostics(dict(getattr(exc, "diagnostics", {}) or {}))
    diagnostics["phase"] = str(phase or "").strip() or "legacy_web_fallback"
    if is_baidu_web_auth_error(exc):
        return BaiduOpenApiError(
            "百度网页登录态已失效，请重新登录百度网盘网页版",
            diagnostics=diagnostics,
        )
    return BaiduOpenApiError(str(exc), diagnostics=diagnostics)


async def _prime_openapi_transfer_via_legacy_web(
    *,
    web_cookie: str,
    share_ctx: Dict[str, Any],
    file_id: str,
) -> Optional[Dict[str, Any]]:
    normalized_cookie = str(web_cookie or "").strip()
    if not normalized_cookie:
        return None
    target_id = str(file_id or "").strip()
    if not target_id:
        return None
    try:
        # 兼容模式这里主要用于完成“公开分享 -> 我的网盘”的转存。
        # 直链返回值在本链路里不直接使用，后续仍回到 OpenAPI 取 dlink。
        await fetch_baidu_download_url(normalized_cookie, share_ctx, target_id)
    except BaiduApiError as exc:
        raise _wrap_legacy_baidu_error(exc, phase="legacy_web_transfer_prime") from exc
    return _merge_legacy_transfer_cache_into_openapi_ctx(share_ctx=share_ctx, file_id=target_id)


def _match_transferred_meta(
    *,
    items: Sequence[Dict[str, Any]],
    expected_name: str = "",
    expected_size: int = 0,
) -> Optional[Dict[str, Any]]:
    normalized_name = str(expected_name or "").strip()
    normalized_size = max(0, int(expected_size or 0))
    fallback: Optional[Dict[str, Any]] = None
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        normalized = _normalize_meta_item(item)
        if bool(normalized.get("is_folder", False)):
            continue
        if fallback is None:
            fallback = normalized
        if normalized_name and str(normalized.get("name", "") or "").strip() != normalized_name:
            continue
        if normalized_size > 0 and max(0, int(normalized.get("size", 0) or 0)) != normalized_size:
            continue
        return normalized
    return fallback


async def _resolve_existing_openapi_transfer_meta(
    *,
    access_token: str,
    candidate_dirs: Sequence[str],
    transfer_fs_id: str = "",
    expected_name: str = "",
    expected_size: int = 0,
) -> Optional[Dict[str, Any]]:
    normalized_transfer_fs_id = str(transfer_fs_id or "").strip()
    if normalized_transfer_fs_id:
        try:
            metas = await _fetch_baidu_openapi_metas_by_fsids(
                access_token=access_token,
                fs_ids=[normalized_transfer_fs_id],
                dlink=False,
            )
        except BaiduOpenApiError:
            metas = []
        if metas:
            normalized = _normalize_meta_item(metas[0], display_name=expected_name)
            if not bool(normalized.get("is_folder", False)):
                return normalized

    for candidate_dir in list(candidate_dirs or []):
        normalized_dir = str(candidate_dir or "").strip()
        if not normalized_dir:
            continue
        try:
            target_items = await _list_baidu_openapi_dir(access_token=access_token, path=normalized_dir)
        except BaiduOpenApiError:
            continue
        matched = _match_transferred_meta(
            items=target_items,
            expected_name=expected_name,
            expected_size=expected_size,
        )
        if matched is not None:
            return matched
    return None


async def _find_openapi_file_by_exact_path(
    *,
    access_token: str,
    path: str,
) -> Optional[Dict[str, Any]]:
    normalized_path = _normalize_openapi_dir_path(path)
    if normalized_path == "/":
        return None
    parent_dir = _normalize_openapi_dir_path(os.path.dirname(normalized_path) or "/")
    try:
        target_items = await _list_baidu_openapi_dir(access_token=access_token, path=parent_dir, folder_only=False)
    except BaiduOpenApiError:
        return None
    for item in list(target_items or []):
        if not isinstance(item, dict):
            continue
        normalized = _normalize_meta_item(item)
        if bool(normalized.get("is_folder", False)):
            continue
        if _normalize_openapi_dir_path(str(normalized.get("path", "") or "").strip()) == normalized_path:
            return normalized
    return None


def _mark_openapi_share_transfer_deleted(
    *,
    share_ctx: Dict[str, Any],
    file_id: str,
) -> None:
    ctx_files = share_ctx.get("files") if isinstance(share_ctx.get("files"), dict) else {}
    if not isinstance(ctx_files, dict):
        return
    target_id = str(file_id or "").strip()
    cached_meta = ctx_files.get(target_id) if isinstance(ctx_files.get(target_id), dict) else {}
    if not isinstance(cached_meta, dict):
        return
    cleaned_meta = dict(cached_meta)
    for key in ("dlink", "transfer_fs_id", "transfer_path", "transfer_folder_path", "transfer_dir"):
        cleaned_meta.pop(key, None)
    cleaned_meta["transfer_cleanup_status"] = "deleted"
    cleaned_meta["transfer_cleanup_at"] = int(time.time())
    ctx_files[target_id] = cleaned_meta


async def delete_baidu_openapi_transfer_file(
    *,
    access_token: str,
    share_ctx: Dict[str, Any],
    file_id: str,
) -> Dict[str, Any]:
    target_id = str(file_id or "").strip()
    if not target_id:
        return {"deleted": False, "skipped": True, "reason": "missing_file_id"}
    if not isinstance(share_ctx, dict):
        return {"deleted": False, "skipped": True, "reason": "missing_share_ctx", "file_id": target_id}

    ctx_files = share_ctx.get("files") if isinstance(share_ctx.get("files"), dict) else {}
    if not isinstance(ctx_files, dict):
        ctx_files = {}
        share_ctx["files"] = ctx_files
    cached_meta = ctx_files.get(target_id) if isinstance(ctx_files.get(target_id), dict) else {}
    expected_name = str(cached_meta.get("name", "") or "").strip()
    expected_size = max(0, int(cached_meta.get("size", 0) or 0))
    share_id = str(share_ctx.get("share_id", "") or "").strip()
    cached_transfer_dir = str(cached_meta.get("transfer_dir", "") or "").strip()
    candidate_dirs = (
        _build_openapi_share_transfer_dir_candidates(
            share_id=share_id,
            file_id=target_id,
            cached_dir=cached_transfer_dir,
        )
        if share_id
        else [path for path in [cached_transfer_dir, str(cached_meta.get("transfer_folder_path", "") or "").strip()] if path]
    )
    transfer_fs_id = str(cached_meta.get("transfer_fs_id", "") or "").strip()
    transfer_path = str(cached_meta.get("transfer_path", "") or "").strip()
    resolved_meta: Optional[Dict[str, Any]] = None

    if not transfer_path or not transfer_fs_id:
        resolved_meta = await _resolve_existing_openapi_transfer_meta(
            access_token=access_token,
            candidate_dirs=candidate_dirs,
            transfer_fs_id=transfer_fs_id,
            expected_name=expected_name,
            expected_size=expected_size,
        )
        if resolved_meta is not None:
            transfer_fs_id = str(resolved_meta.get("file_id", "") or "").strip() or transfer_fs_id
            transfer_path = str(resolved_meta.get("path", "") or "").strip() or transfer_path
            merged_meta = _merge_openapi_share_file_meta(
                cached_meta,
                {
                    "file_id": target_id,
                    "name": expected_name or str(resolved_meta.get("name", "") or "").strip(),
                    "size": expected_size or max(0, int(resolved_meta.get("size", 0) or 0)),
                    "is_folder": False,
                    "transfer_fs_id": transfer_fs_id,
                    "transfer_path": transfer_path,
                    "transfer_folder_path": str(resolved_meta.get("folder_path", "") or "").strip(),
                    "transfer_dir": str(resolved_meta.get("folder_path", "") or "").strip(),
                },
            )
            ctx_files[target_id] = merged_meta
            cached_meta = merged_meta

    if not transfer_path:
        return {
            "deleted": False,
            "skipped": True,
            "reason": "missing_transfer_path",
            "file_id": target_id,
        }

    request_data = {
        "async": "0",
        "filelist": json.dumps([transfer_path], ensure_ascii=False),
    }
    diagnostics = {
        "share_id": share_id,
        "file_id": target_id,
        "transfer_path": transfer_path,
        "transfer_fs_id": transfer_fs_id,
    }
    try:
        payload = await _request_json(
            "POST",
            BAIDU_OPENAPI_XPAN_FILE_URL,
            params={
                "method": "filemanager",
                "opera": "delete",
                "access_token": str(access_token or "").strip(),
            },
            data=request_data,
            headers={
                "User-Agent": _DOWNLOAD_UA,
                "Referer": "https://pan.baidu.com/disk/home",
            },
        )
        _raise_api_error("删除百度网盘官方转存文件失败", payload=payload, diagnostics=diagnostics)
    except BaiduOpenApiError:
        still_exists = False
        if transfer_fs_id:
            try:
                metas = await _fetch_baidu_openapi_metas_by_fsids(
                    access_token=access_token,
                    fs_ids=[transfer_fs_id],
                    dlink=False,
                )
            except BaiduOpenApiError:
                metas = []
            still_exists = bool(metas)
        if not still_exists and transfer_path:
            existing_by_path = await _find_openapi_file_by_exact_path(
                access_token=access_token,
                path=transfer_path,
            )
            still_exists = existing_by_path is not None
        if still_exists:
            raise
        _mark_openapi_share_transfer_deleted(share_ctx=share_ctx, file_id=target_id)
        return {
            "deleted": True,
            "already_missing": True,
            "file_id": target_id,
            "path": transfer_path,
        }

    _mark_openapi_share_transfer_deleted(share_ctx=share_ctx, file_id=target_id)
    return {
        "deleted": True,
        "already_missing": False,
        "file_id": target_id,
        "path": transfer_path,
    }


async def resolve_baidu_openapi_resource(
    *,
    access_token: str,
    descriptor: Dict[str, Any],
) -> Tuple[ResolvedShare, Dict[str, Any]]:
    path = str(descriptor.get("path", "") or "").strip()
    fs_id = str(descriptor.get("fs_id", "") or descriptor.get("fsid", "") or "").strip()
    display_name = str(descriptor.get("display_name", "") or descriptor.get("name", "") or "").strip()
    is_dir_hint = str(descriptor.get("is_dir", descriptor.get("dir", "")) or "").strip().lower()
    files_meta: List[Dict[str, Any]] = []
    resource_display_name = display_name

    async def _append_meta_by_fs_id(target_fs_id: str, *, preferred_name: str = "") -> bool:
        normalized_target = str(target_fs_id or "").strip()
        if not normalized_target:
            return False
        metas = await _fetch_baidu_openapi_metas_by_fsids(access_token=access_token, fs_ids=[normalized_target], dlink=False)
        if not metas:
            return False
        normalized_meta = _normalize_meta_item(metas[0], display_name=preferred_name or resource_display_name)
        files_meta.append(normalized_meta)
        return True

    if path:
        if path.endswith("/"):
            is_dir_hint = "1"
            path = path.rstrip("/") or "/"
        if path == "/":
            is_dir_hint = "1"
        if is_dir_hint in {"1", "true", "yes", "dir", "folder"}:
            for item in await _list_baidu_openapi_dir(access_token=access_token, path=path):
                files_meta.append(_normalize_meta_item(item))
            resource_display_name = resource_display_name or os.path.basename(path.rstrip("/")) or "/"
        else:
            parent_dir = os.path.dirname(path.rstrip("/")) or "/"
            target_name = os.path.basename(path.rstrip("/"))
            parent_items = await _list_baidu_openapi_dir(access_token=access_token, path=parent_dir)
            matched = None
            for item in parent_items:
                current_path = str(item.get("path", "") or "").strip()
                current_name = str(item.get("server_filename", "") or "").strip()
                if current_path == path or current_name == target_name:
                    matched = item
                    break
            if matched is None:
                raise BaiduOpenApiError(
                    "未找到百度网盘官方资源路径",
                    diagnostics={"path": path, "parent_dir": parent_dir},
                )
            normalized_matched = _normalize_meta_item(matched, display_name=resource_display_name)
            if str(normalized_matched.get("file_id", "") or "").strip():
                files_meta.append(normalized_matched)
            elif not await _append_meta_by_fs_id(fs_id, preferred_name=str(normalized_matched.get("name", "") or "")):
                files_meta.append(normalized_matched)
            resource_display_name = resource_display_name or str(files_meta[0].get("name", "") or "").strip()
    elif fs_id:
        if not await _append_meta_by_fs_id(fs_id):
            raise BaiduOpenApiError("未找到百度网盘官方资源", diagnostics={"fs_id": fs_id})
        resource_display_name = resource_display_name or str(files_meta[0].get("name", "") or "").strip()
    else:
        raise BaiduOpenApiError("百度网盘官方资源描述符缺少 path 或 fs_id", diagnostics={"descriptor": descriptor})

    resolved_files = [
        ResolvedFile(
            file_id=str(item.get("file_id", "") or ""),
            name=str(item.get("name", "") or ""),
            size=max(0, int(item.get("size", 0) or 0)),
            is_folder=bool(item.get("is_folder", False)),
            path=str(item.get("path", "") or ""),
            folder_path=str(item.get("folder_path", "") or ""),
        )
        for item in files_meta
        if str(item.get("file_id", "") or "").strip()
    ]
    if not resolved_files and fs_id:
        files_meta = []
        if await _append_meta_by_fs_id(fs_id):
            resolved_files = [
                ResolvedFile(
                    file_id=str(item.get("file_id", "") or ""),
                    name=str(item.get("name", "") or ""),
                    size=max(0, int(item.get("size", 0) or 0)),
                    is_folder=bool(item.get("is_folder", False)),
                    path=str(item.get("path", "") or ""),
                    folder_path=str(item.get("folder_path", "") or ""),
                )
                for item in files_meta
                if str(item.get("file_id", "") or "").strip()
            ]
    if not resolved_files:
        raise BaiduOpenApiError("百度网盘官方资源为空", diagnostics={"descriptor": descriptor})

    share_ctx = {
        "provider": "baidu_openapi",
        "client_rev": BAIDU_OPENAPI_CLIENT_REV,
        "descriptor": {
            "path": path,
            "fs_id": fs_id,
            "display_name": resource_display_name,
            "is_dir": is_dir_hint,
        },
        "files": {
            str(item.get("file_id", "") or ""): {
                "file_id": str(item.get("file_id", "") or ""),
                "path": str(item.get("path", "") or ""),
                "name": str(item.get("name", "") or ""),
                "size": max(0, int(item.get("size", 0) or 0)),
                "is_folder": bool(item.get("is_folder", False)),
                "dlink": str(item.get("dlink", "") or ""),
            }
            for item in files_meta
            if str(item.get("file_id", "") or "").strip()
        },
    }
    resolved = ResolvedShare(
        share_code="baidu-openapi",
        share_id=path or fs_id or resource_display_name or "baidu-openapi",
        pwd="",
        files=resolved_files,
    )
    return resolved, share_ctx


async def resolve_baidu_openapi_share_url(*, share_url: str) -> Tuple[ResolvedShare, Dict[str, Any]]:
    try:
        resolved, legacy_ctx = await resolve_baidu_share(share_url, "")
    except BaiduApiError as exc:
        raise BaiduOpenApiError(str(exc), diagnostics=_sanitize_diagnostics(getattr(exc, "diagnostics", {}) or {})) from exc

    share_ctx = {
        "provider": "baidu_openapi",
        "client_rev": BAIDU_OPENAPI_CLIENT_REV,
        "share_source": "public_share_url",
        "share_url": str(legacy_ctx.get("share_url", "") or share_url).strip(),
        "canonical_url": str(legacy_ctx.get("canonical_url", "") or legacy_ctx.get("share_url", "") or share_url).strip(),
        "share_id": str(legacy_ctx.get("share_id", "") or resolved.share_id or "").strip(),
        "uk": str(legacy_ctx.get("uk", "") or "").strip(),
        "surl": str(legacy_ctx.get("surl", "") or resolved.share_code or "").strip(),
        "pwd": str(legacy_ctx.get("pwd", "") or resolved.pwd or "").strip(),
        "randsk": str(legacy_ctx.get("randsk", "") or "").strip(),
        "sekey": str(legacy_ctx.get("sekey", "") or "").strip(),
        "files": {},
    }
    ctx_files = share_ctx["files"]
    for item in list(resolved.files or []):
        file_id = str(getattr(item, "file_id", "") or "").strip()
        if not file_id:
            continue
        ctx_files[file_id] = {
            "file_id": file_id,
            "name": str(getattr(item, "name", "") or "").strip(),
            "size": max(0, int(getattr(item, "size", 0) or 0)),
            "is_folder": bool(getattr(item, "is_folder", False)),
            "path": str(getattr(item, "path", "") or "").strip(),
            "folder_path": str(getattr(item, "folder_path", "") or "").strip(),
        }
    return resolved, share_ctx


def _append_access_token_to_dlink(dlink: str, access_token: str) -> str:
    direct_url = str(dlink or "").strip()
    if not direct_url:
        return ""
    try:
        parsed = urlparse(direct_url)
        query = parse_qs(parsed.query or "")
        if not query.get("access_token"):
            query["access_token"] = [str(access_token or "").strip()]
        if not query.get("clienttype"):
            query["clienttype"] = ["0"]
        if not query.get("web"):
            query["web"] = ["1"]
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode({key: values[-1] for key, values in query.items() if values}, doseq=False),
                parsed.fragment,
            )
        )
    except Exception:
        joiner = "&" if "?" in direct_url else "?"
        return f"{direct_url}{joiner}access_token={access_token}&clienttype=0&web=1"


async def _transfer_baidu_openapi_share_file(
    *,
    access_token: str,
    share_ctx: Dict[str, Any],
    file_id: str,
) -> Dict[str, Any]:
    target_id = str(file_id or "").strip()
    if not target_id:
        raise BaiduOpenApiError("百度网盘分享资源 file_id 为空")

    share_id = str(share_ctx.get("share_id", "") or "").strip()
    uk = str(share_ctx.get("uk", "") or "").strip()
    sekey = str(share_ctx.get("sekey", "") or "").strip()
    if not share_id or not uk:
        raise BaiduOpenApiError(
            "百度网盘分享上下文不完整",
            diagnostics={"file_id": target_id, "share_id": share_id, "uk": uk},
        )

    try:
        int(target_id)
    except Exception as exc:
        raise BaiduOpenApiError("百度网盘分享资源 file_id 无效", diagnostics={"file_id": target_id}) from exc

    ctx_files = share_ctx.get("files") if isinstance(share_ctx.get("files"), dict) else {}
    if not isinstance(ctx_files, dict):
        ctx_files = {}
        share_ctx["files"] = ctx_files
    cached_meta = ctx_files.get(target_id) if isinstance(ctx_files.get(target_id), dict) else {}
    expected_name = str(cached_meta.get("name", "") or "").strip()
    expected_size = max(0, int(cached_meta.get("size", 0) or 0))
    cached_transfer_dir = str(cached_meta.get("transfer_dir", "") or "").strip()
    transfer_dir_candidates = _build_openapi_share_transfer_dir_candidates(
        share_id=share_id,
        file_id=target_id,
        cached_dir=cached_transfer_dir,
    )
    cached_transfer_fs_id = str(cached_meta.get("transfer_fs_id", "") or "").strip()

    resolved_transfer_meta = await _resolve_existing_openapi_transfer_meta(
        access_token=access_token,
        candidate_dirs=[cached_transfer_dir] if cached_transfer_dir else [],
        transfer_fs_id=cached_transfer_fs_id,
        expected_name=expected_name,
        expected_size=expected_size,
    )
    if resolved_transfer_meta is not None:
        existing_transfer_dir = (
            str(resolved_transfer_meta.get("folder_path", "") or "").strip()
            or str(cached_meta.get("transfer_dir", "") or "").strip()
            or transfer_dir_candidates[0]
        )
        merged_meta = _merge_openapi_share_file_meta(
            cached_meta,
            {
                "file_id": target_id,
                "name": expected_name or resolved_transfer_meta.get("name", ""),
                "size": expected_size or resolved_transfer_meta.get("size", 0),
                "is_folder": False,
                "transfer_dir": existing_transfer_dir,
                "transfer_fs_id": str(resolved_transfer_meta.get("file_id", "") or "").strip(),
                "transfer_path": str(resolved_transfer_meta.get("path", "") or "").strip(),
                "transfer_folder_path": str(resolved_transfer_meta.get("folder_path", "") or existing_transfer_dir).strip(),
            },
        )
        ctx_files[target_id] = merged_meta
        return merged_meta

    payload_data: Dict[str, Any] = {
        "shareid": share_id,
        "from": uk,
        "fsidlist": json.dumps([int(target_id)], ensure_ascii=False),
        "async": 0,
        "ondup": "newcopy",
    }
    if sekey:
        payload_data["sekey"] = sekey

    payload: Dict[str, Any] = {}
    transfer_dir = transfer_dir_candidates[0]
    last_transfer_error: Optional[BaiduOpenApiError] = None
    attempted_dirs: List[str] = []
    for candidate_dir in transfer_dir_candidates:
        transfer_dir = str(candidate_dir or "").strip() or transfer_dir
        attempted_dirs.append(transfer_dir)
        for attempt_index in range(3):
            try:
                await _ensure_openapi_dir_exists(access_token=access_token, path=transfer_dir)
                payload = await _request_json(
                    "POST",
                    BAIDU_OPENAPI_XPAN_SHARE_URL,
                    params={
                        "method": "transfer",
                        "access_token": str(access_token or "").strip(),
                    },
                    data={
                        **payload_data,
                        "path": transfer_dir,
                    },
                    headers={
                        "User-Agent": _DOWNLOAD_UA,
                        "Referer": "pan.baidu.com",
                    },
                )
                _raise_api_error(
                    "百度网盘官方转存失败",
                    payload=payload,
                    diagnostics={
                        "share_id": share_id,
                        "uk": uk,
                        "file_id": target_id,
                        "transfer_dir": transfer_dir,
                        "attempted_dirs": list(attempted_dirs),
                        "attempt_index": attempt_index + 1,
                    },
                )
                last_transfer_error = None
                break
            except BaiduOpenApiError as exc:
                last_transfer_error = exc
                recovered_meta = await _resolve_existing_openapi_transfer_meta(
                    access_token=access_token,
                    candidate_dirs=attempted_dirs,
                    transfer_fs_id=cached_transfer_fs_id,
                    expected_name=expected_name,
                    expected_size=expected_size,
                )
                if recovered_meta is not None:
                    merged_meta = _merge_openapi_share_file_meta(
                        cached_meta,
                        {
                            "file_id": target_id,
                            "name": expected_name or recovered_meta.get("name", ""),
                            "size": expected_size or recovered_meta.get("size", 0),
                            "is_folder": False,
                            "transfer_dir": str(recovered_meta.get("folder_path", "") or transfer_dir).strip() or transfer_dir,
                            "transfer_fs_id": str(recovered_meta.get("file_id", "") or "").strip(),
                            "transfer_path": str(recovered_meta.get("path", "") or "").strip(),
                            "transfer_folder_path": str(recovered_meta.get("folder_path", "") or transfer_dir).strip() or transfer_dir,
                        },
                    )
                    ctx_files[target_id] = merged_meta
                    return merged_meta
                if is_baidu_openapi_auth_error(exc):
                    raise
                if attempt_index < 2 and is_baidu_openapi_retryable_transfer_error(exc):
                    await asyncio.sleep(0.6 * (attempt_index + 1))
                    continue
                break
        if last_transfer_error is None:
            break

    if last_transfer_error is not None:
        raise last_transfer_error

    transfer_fs_id = ""
    transfer_path = ""
    transfer_name = expected_name
    for container in (payload.get("info"), payload.get("extra")):
        if isinstance(container, dict):
            for key in ("fsid", "to_fs_id", "toFsid"):
                value = str(container.get(key, "") or "").strip()
                if value:
                    transfer_fs_id = transfer_fs_id or value
            for key in ("path", "to", "to_path"):
                value = str(container.get(key, "") or "").strip()
                if value:
                    transfer_path = transfer_path or value
            for key in ("server_filename", "filename", "name"):
                value = str(container.get(key, "") or "").strip()
                if value:
                    transfer_name = transfer_name or value
        elif isinstance(container, list):
            for item in container:
                if not isinstance(item, dict):
                    continue
                transfer_fs_id = transfer_fs_id or str(
                    item.get("fsid", "") or item.get("to_fs_id", "") or item.get("toFsid", "") or ""
                ).strip()
                transfer_path = transfer_path or str(item.get("path", "") or item.get("to", "") or item.get("to_path", "")).strip()
                transfer_name = transfer_name or str(
                    item.get("server_filename", "") or item.get("filename", "") or item.get("name", "") or ""
                ).strip()
                if transfer_fs_id and transfer_path:
                    break
        if transfer_fs_id and transfer_path:
            break

    resolved_transfer_meta = None
    for attempt_index in range(10):
        if attempt_index > 0:
            await asyncio.sleep(0.45)
        resolved_transfer_meta = await _resolve_existing_openapi_transfer_meta(
            access_token=access_token,
            candidate_dirs=transfer_dir_candidates,
            transfer_fs_id=transfer_fs_id,
            expected_name=transfer_name,
            expected_size=expected_size,
        )
        if resolved_transfer_meta is not None:
            break

    if resolved_transfer_meta is None and transfer_fs_id:
        resolved_transfer_meta = {
            "file_id": transfer_fs_id,
            "name": transfer_name,
            "size": expected_size,
            "is_folder": False,
            "path": transfer_path,
            "folder_path": transfer_dir,
        }

    if resolved_transfer_meta is None:
        raise BaiduOpenApiError(
            "百度网盘官方转存成功，但未获取到转存文件元数据",
            diagnostics={"share_id": share_id, "uk": uk, "file_id": target_id, "transfer_dir": transfer_dir},
        )

    merged_meta = _merge_openapi_share_file_meta(
        cached_meta,
        {
            "file_id": target_id,
            "name": transfer_name or resolved_transfer_meta.get("name", ""),
            "size": expected_size or resolved_transfer_meta.get("size", 0),
            "is_folder": False,
            "transfer_dir": str(resolved_transfer_meta.get("folder_path", "") or transfer_dir).strip() or transfer_dir,
            "transfer_fs_id": str(resolved_transfer_meta.get("file_id", "") or "").strip(),
            "transfer_path": str(resolved_transfer_meta.get("path", "") or transfer_path).strip(),
            "transfer_folder_path": str(resolved_transfer_meta.get("folder_path", "") or transfer_dir).strip(),
        },
    )
    ctx_files[target_id] = merged_meta
    return merged_meta


async def _transfer_baidu_openapi_share_files(
    *,
    access_token: str,
    share_ctx: Dict[str, Any],
    file_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """批量转存公开分享中的多个文件，减少逐文件转存触发的超时。"""
    target_ids: List[str] = []
    for raw in list(file_ids or []):
        text = str(raw or "").strip()
        if not text or text in target_ids:
            continue
        try:
            int(text)
        except Exception:
            continue
        target_ids.append(text)
    if not target_ids:
        return {}
    if len(target_ids) == 1:
        single = await _transfer_baidu_openapi_share_file(
            access_token=access_token,
            share_ctx=share_ctx,
            file_id=target_ids[0],
        )
        return {target_ids[0]: dict(single)}

    share_id = str(share_ctx.get("share_id", "") or "").strip()
    uk = str(share_ctx.get("uk", "") or "").strip()
    sekey = str(share_ctx.get("sekey", "") or "").strip()
    if not share_id or not uk:
        raise BaiduOpenApiError(
            "百度网盘分享上下文不完整",
            diagnostics={"share_id": share_id, "uk": uk, "file_ids": list(target_ids)},
        )

    ctx_files = share_ctx.get("files") if isinstance(share_ctx.get("files"), dict) else {}
    if not isinstance(ctx_files, dict):
        ctx_files = {}
        share_ctx["files"] = ctx_files

    transfer_dir = _build_openapi_share_transfer_batch_dir(share_id=share_id, file_ids=target_ids)
    payload_data: Dict[str, Any] = {
        "shareid": share_id,
        "from": uk,
        "fsidlist": json.dumps([int(item) for item in target_ids], ensure_ascii=False),
        "async": 0,
        "ondup": "newcopy",
    }
    if sekey:
        payload_data["sekey"] = sekey

    last_error: Optional[BaiduOpenApiError] = None
    for attempt_index in range(3):
        try:
            await _ensure_openapi_dir_exists(access_token=access_token, path=transfer_dir)
            payload = await _request_json(
                "POST",
                BAIDU_OPENAPI_XPAN_SHARE_URL,
                params={
                    "method": "transfer",
                    "access_token": str(access_token or "").strip(),
                },
                data={
                    **payload_data,
                    "path": transfer_dir,
                },
                headers={
                    "User-Agent": _DOWNLOAD_UA,
                    "Referer": "pan.baidu.com",
                },
            )
            _raise_api_error(
                "百度网盘官方批量转存失败",
                payload=payload,
                diagnostics={
                    "share_id": share_id,
                    "uk": uk,
                    "file_ids": list(target_ids),
                    "transfer_dir": transfer_dir,
                    "attempt_index": attempt_index + 1,
                },
            )
            last_error = None
            break
        except BaiduOpenApiError as exc:
            last_error = exc
            if is_baidu_openapi_auth_error(exc):
                raise
            if attempt_index < 2 and is_baidu_openapi_retryable_transfer_error(exc):
                await asyncio.sleep(0.8 * (attempt_index + 1))
                continue
            break

    if last_error is not None:
        raise last_error

    dir_items: List[Dict[str, Any]] = []
    for attempt_index in range(10):
        if attempt_index > 0:
            await asyncio.sleep(0.45)
        try:
            dir_items = await _list_baidu_openapi_dir(
                access_token=access_token,
                path=transfer_dir,
                folder_only=False,
            )
        except BaiduOpenApiError:
            continue
        non_folder_items = [item for item in dir_items if not bool(_normalize_meta_item(item).get("is_folder", False))]
        if len(non_folder_items) >= len(target_ids):
            break

    normalized_candidates = [
        _normalize_meta_item(item)
        for item in list(dir_items or [])
        if isinstance(item, dict)
    ]
    updates: Dict[str, Dict[str, Any]] = {}
    for target_id in target_ids:
        cached_meta = ctx_files.get(target_id) if isinstance(ctx_files.get(target_id), dict) else {}
        expected_name = str(cached_meta.get("name", "") or "").strip()
        expected_size = max(0, int(cached_meta.get("size", 0) or 0))
        matched = _match_transferred_meta(
            items=normalized_candidates,
            expected_name=expected_name,
            expected_size=expected_size,
        )
        if matched is None:
            continue
        merged_meta = _merge_openapi_share_file_meta(
            cached_meta,
            {
                "file_id": target_id,
                "name": expected_name or str(matched.get("name", "") or "").strip(),
                "size": expected_size or max(0, int(matched.get("size", 0) or 0)),
                "is_folder": False,
                "transfer_dir": transfer_dir,
                "transfer_fs_id": str(matched.get("file_id", "") or "").strip(),
                "transfer_path": str(matched.get("path", "") or "").strip(),
                "transfer_folder_path": str(matched.get("folder_path", "") or transfer_dir).strip() or transfer_dir,
            },
        )
        ctx_files[target_id] = merged_meta
        updates[target_id] = merged_meta
    return updates


async def fetch_baidu_openapi_download_url(
    *,
    access_token: str,
    share_ctx: Dict[str, Any],
    file_id: str,
    web_cookie: str = "",
) -> str:
    target_id = str(file_id or "").strip()
    if not target_id:
        raise BaiduOpenApiError("百度网盘官方资源 file_id 为空")

    ctx_files = share_ctx.get("files") if isinstance(share_ctx.get("files"), dict) else {}
    cached_meta = ctx_files.get(target_id) if isinstance(ctx_files, dict) else None
    dlink = str(isinstance(cached_meta, dict) and cached_meta.get("dlink") or "").strip()
    if not dlink:
        target_fs_id = target_id
        share_source = str(share_ctx.get("share_source", "") or "").strip().lower()
        if share_source == "public_share_url" and str(web_cookie or "").strip():
            try:
                primed_meta = await _prime_openapi_transfer_via_legacy_web(
                    web_cookie=str(web_cookie or "").strip(),
                    share_ctx=share_ctx,
                    file_id=target_id,
                )
            except BaiduOpenApiError:
                # 兼容模式转存只是优先策略，失败后仍可继续尝试 OpenAPI 自身转存。
                pass
            else:
                if isinstance(primed_meta, dict):
                    cached_meta = primed_meta
                    primed_transfer_fs_id = str(primed_meta.get("transfer_fs_id", "") or "").strip()
                    if primed_transfer_fs_id:
                        target_fs_id = primed_transfer_fs_id
        if share_source == "public_share_url":
            cached_transfer_fs_id = str(isinstance(cached_meta, dict) and cached_meta.get("transfer_fs_id") or "").strip()
            if cached_transfer_fs_id:
                target_fs_id = cached_transfer_fs_id
            else:
                selected_file_ids = [
                    str(item or "").strip()
                    for item in list(share_ctx.get("_selected_file_ids") or [])
                    if str(item or "").strip()
                ] if isinstance(share_ctx.get("_selected_file_ids"), list) else []
                should_try_batch_transfer = (
                    not bool(share_ctx.get("_batch_transfer_attempted"))
                    and len(selected_file_ids) > 1
                    and target_id in selected_file_ids
                )
                if should_try_batch_transfer:
                    share_ctx["_batch_transfer_attempted"] = True
                    try:
                        _ = await _transfer_baidu_openapi_share_files(
                            access_token=access_token,
                            share_ctx=share_ctx,
                            file_ids=selected_file_ids,
                        )
                    except BaiduOpenApiError:
                        # 批量转存只是优化，失败后仍回退到原有单文件链路。
                        pass
                    cached_meta = ctx_files.get(target_id) if isinstance(ctx_files, dict) and isinstance(ctx_files.get(target_id), dict) else cached_meta
                    cached_transfer_fs_id = str(isinstance(cached_meta, dict) and cached_meta.get("transfer_fs_id") or "").strip()
                    if cached_transfer_fs_id:
                        target_fs_id = cached_transfer_fs_id
                if target_fs_id == target_id:
                    try:
                        transfer_meta = await _transfer_baidu_openapi_share_file(
                            access_token=access_token,
                            share_ctx=share_ctx,
                            file_id=target_id,
                        )
                        cached_meta = transfer_meta
                        target_fs_id = str(transfer_meta.get("transfer_fs_id", "") or "").strip() or target_id
                    except BaiduOpenApiError:
                        if not str(web_cookie or "").strip():
                            raise
                        try:
                            primed_meta = await _prime_openapi_transfer_via_legacy_web(
                                web_cookie=str(web_cookie or "").strip(),
                                share_ctx=share_ctx,
                                file_id=target_id,
                            )
                        except BaiduOpenApiError:
                            raise
                        cached_meta = primed_meta if isinstance(primed_meta, dict) else cached_meta
                        target_fs_id = (
                            str(isinstance(cached_meta, dict) and cached_meta.get("transfer_fs_id") or "").strip()
                            or target_id
                        )
        elif isinstance(cached_meta, dict):
            target_fs_id = str(cached_meta.get("transfer_fs_id", "") or "").strip() or target_id

        try:
            metas = await _fetch_baidu_openapi_metas_by_fsids(access_token=access_token, fs_ids=[target_fs_id], dlink=True)
        except BaiduOpenApiError:
            if share_source != "public_share_url":
                raise
            try:
                transfer_meta = await _transfer_baidu_openapi_share_file(
                    access_token=access_token,
                    share_ctx=share_ctx,
                    file_id=target_id,
                )
                cached_meta = transfer_meta
                target_fs_id = str(transfer_meta.get("transfer_fs_id", "") or "").strip() or target_id
                metas = await _fetch_baidu_openapi_metas_by_fsids(access_token=access_token, fs_ids=[target_fs_id], dlink=True)
            except BaiduOpenApiError:
                if not str(web_cookie or "").strip():
                    raise
                try:
                    primed_meta = await _prime_openapi_transfer_via_legacy_web(
                        web_cookie=str(web_cookie or "").strip(),
                        share_ctx=share_ctx,
                        file_id=target_id,
                    )
                except BaiduOpenApiError:
                    raise
                cached_meta = primed_meta if isinstance(primed_meta, dict) else cached_meta
                target_fs_id = (
                    str(isinstance(cached_meta, dict) and cached_meta.get("transfer_fs_id") or "").strip()
                    or target_id
                )
                metas = await _fetch_baidu_openapi_metas_by_fsids(access_token=access_token, fs_ids=[target_fs_id], dlink=True)
        if not metas:
            raise BaiduOpenApiError("未找到百度网盘官方下载资源", diagnostics={"file_id": target_id})
        normalized = _normalize_meta_item(metas[0], display_name=str(isinstance(cached_meta, dict) and cached_meta.get("name") or ""))
        dlink = str(normalized.get("dlink", "") or "").strip()
        if not dlink:
            raise BaiduOpenApiError("百度网盘官方资源未返回下载地址", diagnostics={"file_id": target_id})
        if not isinstance(ctx_files, dict):
            ctx_files = {}
            share_ctx["files"] = ctx_files
        if share_source == "public_share_url":
            ctx_files[target_id] = _merge_openapi_share_file_meta(
                cached_meta if isinstance(cached_meta, dict) else {},
                {
                    "dlink": dlink,
                    "transfer_fs_id": str(normalized.get("file_id", "") or "").strip(),
                    "transfer_path": str(normalized.get("path", "") or "").strip(),
                    "transfer_folder_path": str(normalized.get("folder_path", "") or "").strip(),
                    "name": str(
                        (cached_meta or {}).get("name", "") if isinstance(cached_meta, dict) else normalized.get("name", "")
                    ).strip()
                    or str(normalized.get("name", "") or "").strip(),
                    "size": max(
                        0,
                        int(
                            (
                                (cached_meta or {}).get("size", 0)
                                if isinstance(cached_meta, dict)
                                else normalized.get("size", 0)
                            )
                            or normalized.get("size", 0)
                        ),
                    ),
                    "is_folder": False,
                },
            )
        else:
            ctx_files[target_id] = normalized
    return _append_access_token_to_dlink(dlink, access_token)


def is_baidu_openapi_auth_error(exc: BaseException) -> bool:
    diagnostics = getattr(exc, "diagnostics", {}) if isinstance(exc, BaiduOpenApiError) else {}
    errno = str((diagnostics or {}).get("errno", "") or "").strip()
    error = str((diagnostics or {}).get("error", "") or "").strip().lower()
    description = str((diagnostics or {}).get("error_description", "") or "").strip().lower()
    error_msg = str((diagnostics or {}).get("error_msg", "") or "").strip().lower()
    if errno in {"-6", "110", "111"}:
        return True
    return any(
        token in f"{error} {description} {error_msg}"
        for token in ("expired", "invalid_token", "access token invalid", "access token expired")
    )


def is_baidu_openapi_retryable_transfer_error(exc: BaseException) -> bool:
    if not isinstance(exc, BaiduOpenApiError):
        return False
    diagnostics = getattr(exc, "diagnostics", {}) or {}
    errno = str(diagnostics.get("errno", "") or "").strip()
    error_msg = str(diagnostics.get("error_msg", "") or "").strip().lower()
    if errno in {"4", "31034", "31045"}:
        return True
    return any(token in error_msg for token in ("timeout", "超时", "稍后再试", "频繁"))
