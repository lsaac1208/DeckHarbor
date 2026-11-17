# baidu_client.py - 百度网盘分享解析与直链获取
#
# Phase 1: 仅供 Freedeck “自定义源导入”使用（分享解析 + 直链下载）。

from __future__ import annotations

import json
import asyncio
import os
import re
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import aiohttp
from yarl import URL

from cloud.models import ShareFile, ShareInfo as ResolvedShare


class BaiduApiError(RuntimeError):
    """百度网盘相关异常。"""

    def __init__(self, message: str, *, diagnostics: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def is_baidu_web_auth_error(exc: BaseException) -> bool:
    if not isinstance(exc, BaiduApiError):
        return False
    diagnostics = getattr(exc, "diagnostics", {}) or {}
    stage = str(diagnostics.get("stage", "") or "").strip().lower()
    if stage == "login":
        return True
    payload = diagnostics.get("payload") if isinstance(diagnostics, dict) else None
    errno = 0
    if isinstance(payload, dict):
        try:
            errno = int(payload.get("errno", payload.get("error_code", 0)) or 0)
        except Exception:
            errno = 0
    message = str(exc or "").strip().lower()
    if errno == -6 and stage in {"gettemplatevariable", "transfer", "filemetas", "dlink_redirect"}:
        return True
    return "缺少 bduss" in message.lower()


_UA = (
    # 说明：Baidu 部分接口会对含 AppleWebKit/Chrome 的 UA 做更严格的校验（缺少 sec-ch-ua 等头会返回 405）。
    # 这里使用更“朴素”的 UA，避免触发 405。
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
)

BAIDU_CLIENT_REV = "2026-03-06.5"

_CA_CANDIDATE_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
    "/etc/openssl/certs/ca-certificates.crt",
)


def _build_ssl_context() -> Tuple[ssl.SSLContext, Dict[str, Any]]:
    """为百度相关请求构建 TLS 上下文（显式指定 CA bundle，兼容 Decky Python）。"""
    diagnostics: Dict[str, Any] = {
        "mode": "verify",
        "selected_ca_file": "",
        "candidate_ca_files": [],
        "candidate_errors": [],
    }

    env_cert_file = str(os.environ.get("SSL_CERT_FILE", "") or "").strip()
    candidates: List[str] = []
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

    dedup_candidates: List[str] = []
    seen = set()
    for raw in candidates:
        path = os.path.realpath(os.path.expanduser(str(raw).strip()))
        if not path or path in seen:
            continue
        seen.add(path)
        dedup_candidates.append(path)

    diagnostics["candidate_ca_files"] = dedup_candidates
    for path in dedup_candidates:
        if not os.path.isfile(path):
            continue
        try:
            # 注意：直接使用 create_default_context(cafile=...) 在部分环境会触发
            # Baidu 侧 405（疑似 WAF/TLS 指纹相关）。改为先创建默认上下文再 load_verify_locations，
            # 兼顾证书可用性与兼容性。
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=path)
            diagnostics["selected_ca_file"] = path
            return context, diagnostics
        except Exception as exc:
            diagnostics["candidate_errors"].append({"path": path, "error": str(exc)})

    context = ssl.create_default_context()
    diagnostics["selected_ca_file"] = "system_default"

    # 仅用于紧急排障，默认不关闭校验。
    insecure_flag = str(os.environ.get("FREEDECK_BAIDU_INSECURE_TLS", "") or "").strip().lower()
    if insecure_flag in {"1", "true", "yes"}:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        diagnostics["mode"] = "insecure"
        diagnostics["selected_ca_file"] = "insecure_env_override"

    return context, diagnostics


def _extract_pwd_from_url(share_url: str) -> str:
    try:
        parsed = urlparse(str(share_url or "").strip())
        query = parse_qs(parsed.query or "")
        pwd = query.get("pwd") or query.get("password") or []
        return str(pwd[0] if pwd else "").strip()
    except Exception:
        return ""


def _extract_surl_from_url(share_url: str) -> str:
    url = str(share_url or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    query = parse_qs(parsed.query or "")
    surl_list = query.get("surl") or []
    if surl_list:
        return str(surl_list[0] or "").strip()
    path = str(parsed.path or "")
    if "/s/" in path:
        code = path.split("/s/", 1)[1].split("/", 1)[0]
        code = code.split("?", 1)[0].strip()
        if code.startswith("1") and len(code) > 1:
            return code[1:]
        return code
    return ""


def _parse_cookie_header(cookie: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    for part in str(cookie or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        kv[name] = value
    return kv


_URL_TRAIL_STRIP_RE = re.compile(r"[)\]】】>＞,，。;；!！?？]+$")


def _sanitize_share_url(raw: str) -> str:
    """从用户粘贴文本中提取可用的百度分享链接。"""
    text = str(raw or "").strip()
    if not text:
        return ""

    def _clean(value: str) -> str:
        value = str(value or "").strip()
        while True:
            new_value = _URL_TRAIL_STRIP_RE.sub("", value).strip()
            if new_value == value:
                break
            value = new_value
        return value

    # 优先提取 https?:// 开头的链接（允许文本中夹杂提取码等信息）。
    for match in re.finditer(r"https?://[^\s]+", text, flags=re.IGNORECASE):
        candidate = _clean(match.group(0))
        lower_candidate = candidate.lower()
        if "pan.baidu.com" in lower_candidate:
            idx2 = lower_candidate.rfind("pan.baidu.com")
            if idx2 >= 0:
                tail2 = _clean(candidate[idx2:])
                if tail2.lower().startswith("pan.baidu.com"):
                    candidate = "https://" + tail2
        try:
            host = str(urlparse(candidate).hostname or "").lower()
        except Exception:
            host = ""
        if host and (host.endswith(".baidu.com") or host == "baidu.com"):
            return candidate

    # 其次尝试提取 pan.baidu.com/xxx 形式的子串。
    m = re.search(r"(?i)(?:www\.|m\.)?pan\.baidu\.com/[^\s]+", text)
    if m:
        return _clean("https://" + m.group(0))

    # 修复类似 "pan.baidu.cpan.baidu.com/..." 的拼接错误：抓取最后一次出现的 pan.baidu.com/
    idx = text.lower().rfind("pan.baidu.com/")
    if idx >= 0:
        tail = _clean(text[idx:])
        return "https://" + tail if not tail.startswith("http") else tail

    return _clean(text)


def _extract_pwd_from_text(text: str) -> str:
    """从粘贴文本中尝试提取提取码/访问码。"""
    raw = str(text or "")
    for pattern in (
        r"(?:提取码|访问码|密码|口令)\s*[:：]?\s*([A-Za-z0-9]{4,8})",
        r"(?:提取码|访问码|密码|口令)\s+([A-Za-z0-9]{4,8})",
    ):
        m = re.search(pattern, raw, flags=re.IGNORECASE)
        if m:
            return str(m.group(1) or "").strip()
    return ""


def _extract_int_field(html: str, key: str) -> int:
    text = str(html or "")
    for pattern in (
        rf"['\"]{re.escape(key)}['\"]\s*:\s*['\"]?(\d+)['\"]?",
        rf"\b{re.escape(key)}\b\s*:\s*['\"]?(\d+)['\"]?",
    ):
        m = re.search(pattern, text)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                continue
    return 0


def _extract_str_field(html: str, key: str) -> str:
    text = str(html or "")
    for pattern in (
        rf"['\"]{re.escape(key)}['\"]\s*:\s*['\"]([^'\"]+)['\"]",
        rf"\b{re.escape(key)}\s*:\s*['\"]([^'\"]+)['\"]",
    ):
        m = re.search(pattern, text)
        if m:
            return str(m.group(1) or "").strip()
    return ""


def _has_bduss(cookie_dict: Dict[str, str]) -> bool:
    """兼容 BDUSS / BDUSS_BFESS 等多种 cookie 名称。"""
    for name in cookie_dict.keys():
        if str(name).startswith("BDUSS"):
            return True
    return False


async def _share_verify(
    session: aiohttp.ClientSession,
    *,
    share_url: str,
    surl: str,
    pwd: str,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> Dict[str, Any]:
    params = {
        "surl": surl,
        "t": str(int(time.time() * 1000)),
        "channel": "chunlei",
        "clienttype": "0",
        "web": "1",
    }
    headers = {
        "Referer": share_url,
        "Origin": "https://pan.baidu.com",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    async with session.post("https://pan.baidu.com/share/verify", params=params, data={"pwd": pwd}, headers=headers) as resp:
        status = int(resp.status)
        if status == 405:
            def _run() -> Dict[str, Any]:
                request_url = "https://pan.baidu.com/share/verify?" + urlencode(params, doseq=True)
                payload_data = urlencode({"pwd": pwd}, doseq=True).encode("utf-8")
                request = urllib_request.Request(request_url, data=payload_data, method="POST")
                request.add_header("User-Agent", _UA)
                request.add_header("Referer", str(share_url or "").strip())
                request.add_header("Origin", "https://pan.baidu.com")
                request.add_header("X-Requested-With", "XMLHttpRequest")
                request.add_header("Accept", "application/json, text/plain, */*")
                request.add_header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
                try:
                    cookie_view = session.cookie_jar.filter_cookies(URL("https://pan.baidu.com/"))
                    cookie_text = "; ".join(
                        f"{key}={morsel.value}"
                        for key, morsel in cookie_view.items()
                        if str(key or "").strip() and str(getattr(morsel, "value", "") or "").strip()
                    )
                except Exception:
                    cookie_text = ""
                if cookie_text:
                    request.add_header("Cookie", cookie_text)
                try:
                    with urllib_request.urlopen(request, timeout=20, context=ssl_context) as urllib_resp:
                        text = urllib_resp.read().decode("utf-8", errors="ignore")
                except urllib_error.HTTPError as exc:
                    text = exc.read().decode("utf-8", errors="ignore")
                    raise BaiduApiError(
                        f"百度网盘验证失败：HTTP {int(getattr(exc, 'code', 0) or 0)}",
                        diagnostics={
                            "stage": "share_verify",
                            "status": int(getattr(exc, "code", 0) or 0),
                            "url": request_url,
                            "body_head": text[:200],
                            "transport": "urllib",
                        },
                    ) from exc
                try:
                    payload = json.loads(text or "{}")
                except Exception as exc:
                    raise BaiduApiError(
                        f"百度网盘验证返回解析失败：{exc}",
                        diagnostics={"stage": "share_verify", "status": 200, "url": request_url, "body_head": text[:200], "transport": "urllib"},
                    ) from exc
                return payload if isinstance(payload, dict) else {}

            return await asyncio.to_thread(_run)
        if status >= 400:
            body = await resp.text()
            raise BaiduApiError(
                f"百度网盘验证失败：HTTP {status}",
                diagnostics={"stage": "share_verify", "status": status, "url": str(resp.url), "body_head": body[:200]},
            )
        try:
            payload = await resp.json(content_type=None)
        except Exception as exc:
            body = await resp.text()
            raise BaiduApiError(
                f"百度网盘验证返回解析失败：{exc}",
                diagnostics={"stage": "share_verify", "status": status, "url": str(resp.url), "body_head": body[:200]},
            ) from exc
    return payload if isinstance(payload, dict) else {}


async def _share_list(
    session: aiohttp.ClientSession,
    *,
    share_url: str,
    share_id: int,
    uk: int,
    page: int = 1,
    num: int = 200,
    dir_path: str = "",
    sekey: str = "",
    ssl_context: Optional[ssl.SSLContext] = None,
) -> Dict[str, Any]:
    dir_text = str(dir_path or "").strip()
    sekey_text = str(sekey or "").strip()
    params = {
        "shareid": str(int(share_id)),
        "uk": str(int(uk)),
        "page": str(int(page)),
        "num": str(int(num)),
        "dir": dir_text,
        # 重要：当前百度接口要求根目录列表必须携带 root=1，否则会返回 errno=2（啊哦，链接出错了）。
        "root": "1" if not dir_text else "0",
        "order": "time",
        "desc": "1",
        "channel": "chunlei",
        "clienttype": "0",
        "web": "1",
        "t": str(int(time.time() * 1000)),
    }
    if sekey_text:
        params["sekey"] = sekey_text
    headers = {
        "Referer": share_url,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    async with session.get("https://pan.baidu.com/share/list", params=params, headers=headers) as resp:
        status = int(resp.status)
        if status == 405:
            def _run() -> Dict[str, Any]:
                request_url = "https://pan.baidu.com/share/list?" + urlencode(params, doseq=True)
                request = urllib_request.Request(request_url, method="GET")
                request.add_header("User-Agent", _UA)
                request.add_header("Referer", str(share_url or "").strip())
                request.add_header("X-Requested-With", "XMLHttpRequest")
                request.add_header("Accept", "application/json, text/plain, */*")
                request.add_header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
                try:
                    cookie_view = session.cookie_jar.filter_cookies(URL("https://pan.baidu.com/"))
                    cookie_text = "; ".join(
                        f"{key}={morsel.value}"
                        for key, morsel in cookie_view.items()
                        if str(key or "").strip() and str(getattr(morsel, "value", "") or "").strip()
                    )
                except Exception:
                    cookie_text = ""
                if cookie_text:
                    request.add_header("Cookie", cookie_text)
                try:
                    with urllib_request.urlopen(request, timeout=20, context=ssl_context) as urllib_resp:
                        text = urllib_resp.read().decode("utf-8", errors="ignore")
                except urllib_error.HTTPError as exc:
                    text = exc.read().decode("utf-8", errors="ignore")
                    raise BaiduApiError(
                        f"百度网盘列表请求失败：HTTP {int(getattr(exc, 'code', 0) or 0)}",
                        diagnostics={
                            "stage": "share_list",
                            "status": int(getattr(exc, "code", 0) or 0),
                            "url": request_url,
                            "body_head": text[:200],
                            "transport": "urllib",
                        },
                    ) from exc
                try:
                    payload = json.loads(text or "{}")
                except Exception as exc:
                    raise BaiduApiError(
                        f"百度网盘列表返回解析失败：{exc}",
                        diagnostics={"stage": "share_list", "status": 200, "url": request_url, "body_head": text[:200], "transport": "urllib"},
                    ) from exc
                return payload if isinstance(payload, dict) else {}

            return await asyncio.to_thread(_run)
        if status >= 400:
            body = await resp.text()
            raise BaiduApiError(
                f"百度网盘列表请求失败：HTTP {status}",
                diagnostics={"stage": "share_list", "status": status, "url": str(resp.url), "body_head": body[:200]},
            )
        try:
            payload = await resp.json(content_type=None)
        except Exception as exc:
            body = await resp.text()
            raise BaiduApiError(
                f"百度网盘列表返回解析失败：{exc}",
                diagnostics={"stage": "share_list", "status": status, "url": str(resp.url), "body_head": body[:200]},
            ) from exc
    return payload if isinstance(payload, dict) else {}


def _is_truthy_flag(value: Any) -> bool:
    """将百度接口的 isdir 等字段规范化为 bool。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return int(value) != 0
        except Exception:
            return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


async def resolve_baidu_share(share_url: str, cookie: str) -> Tuple[ResolvedShare, Dict[str, Any]]:
    """解析百度网盘分享链接，返回文件列表与下载上下文。"""
    url = _sanitize_share_url(share_url)
    if not url:
        raise BaiduApiError("分享链接为空")

    # 从粘贴文本兜底提取提取码（优先用 URL 里的 ?pwd=...）
    surl = _extract_surl_from_url(url)
    if not surl:
        raise BaiduApiError("百度网盘链接无效，缺少 sharecode")
    pwd = _extract_pwd_from_url(url) or _extract_pwd_from_text(share_url)

    # 尽量使用稳定的 init 页面做入口，减少短链跳转差异。
    canonical_url = url
    try:
        host = str(urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    if host.endswith("pan.baidu.com"):
        canonical_url = f"https://pan.baidu.com/share/init?surl={surl}"
        if pwd:
            canonical_url = canonical_url + "&pwd=" + pwd
    canonical_url_no_pwd = f"https://pan.baidu.com/share/init?surl={surl}"
    s_url = f"https://pan.baidu.com/s/1{surl}"
    s_url_with_pwd = s_url + ("?pwd=" + pwd if pwd else "")

    timeout = aiohttp.ClientTimeout(total=14)
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    cookie_dict = _parse_cookie_header(cookie)

    ssl_context, tls_diag = _build_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers,
        connector=connector,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    ) as session:
        # 预热一次首页以获取基础 Cookie（某些网络环境下可降低 405/风控概率）。
        try:
            async with session.get("https://pan.baidu.com/", headers={"Referer": "https://pan.baidu.com/"}, allow_redirects=True) as resp:
                await resp.text()
        except Exception:
            pass

        if cookie_dict:
            try:
                session.cookie_jar.update_cookies(cookie_dict, response_url=URL("https://pan.baidu.com/"))
            except Exception:
                pass

        async def fetch_html(target_url: str, *, referer: str) -> Tuple[str, str, int, Dict[str, str]]:
            async def _fetch_html_via_urllib() -> Tuple[str, str, int, Dict[str, str]]:
                def _run() -> Tuple[str, str, int, Dict[str, str]]:
                    request = urllib_request.Request(str(target_url or "").strip(), method="GET")
                    request.add_header("User-Agent", str(headers.get("User-Agent", _UA)))
                    request.add_header("Accept", str(headers.get("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")))
                    request.add_header("Accept-Language", str(headers.get("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")))
                    request.add_header("Referer", str(referer or "https://pan.baidu.com/"))
                    effective_cookie = str(cookie or "").strip()
                    if effective_cookie:
                        request.add_header("Cookie", effective_cookie)
                    try:
                        with urllib_request.urlopen(request, timeout=20, context=ssl_context) as resp:
                            status = int(getattr(resp, "status", 200) or 200)
                            final = str(getattr(resp, "geturl", lambda: target_url)() or target_url)
                            body = resp.read().decode("utf-8", errors="ignore")
                            headers_view = dict(getattr(resp, "headers", {}) or {})
                            return body, final, status, headers_view
                    except urllib_error.HTTPError as exc:
                        status = int(getattr(exc, "code", 0) or 0)
                        final = str(getattr(exc, "geturl", lambda: target_url)() or target_url)
                        body = exc.read().decode("utf-8", errors="ignore")
                        headers_view = dict(getattr(exc, "headers", {}) or {})
                        return body, final, status, headers_view

                return await asyncio.to_thread(_run)

            async with session.get(target_url, headers={"Referer": referer}, allow_redirects=True) as resp:
                status = int(resp.status)
                final_url = str(resp.url)
                html = await resp.text()
                headers_view = dict(resp.headers)
                if status == 405 and "pan.baidu.com" in str(urlparse(target_url).hostname or "").lower():
                    return await _fetch_html_via_urllib()
                return html, final_url, status, headers_view

        candidates: List[str] = []
        for candidate in (canonical_url, canonical_url_no_pwd, s_url_with_pwd, s_url, url):
            candidate = str(candidate or "").strip()
            if not candidate:
                continue
            if candidate not in candidates:
                candidates.append(candidate)

        html = ""
        final_url = ""
        status = 0
        used_url = ""
        resp_headers: Dict[str, str] = {}
        attempts: List[Dict[str, Any]] = []
        redirect_statuses = {301, 302, 303, 307, 308}
        selected_probe: Optional[Dict[str, Any]] = None
        best_probe: Optional[Dict[str, Any]] = None
        for candidate in candidates:
            try:
                html, final_url, status, resp_headers = await fetch_html(candidate, referer="https://pan.baidu.com/")
                used_url = candidate
            except aiohttp.ClientConnectorCertificateError as exc:
                attempts.append({"url": candidate, "error": f"ssl_verify_failed:{exc}"})
                continue
            except aiohttp.ClientError as exc:
                attempts.append({"url": candidate, "error": str(exc)})
                continue
            except Exception as exc:
                attempts.append({"url": candidate, "error": str(exc)})
                continue

            redirect_location = str(resp_headers.get("Location", "") or "").strip()
            # 有些环境会返回非 2xx 状态但仍携带分享页 HTML（例如 405 被用作风控拦截码）。
            share_id_hint = _extract_int_field(html, "shareid") or _extract_int_field(html, "shareId")
            uk_hint = _extract_int_field(html, "share_uk") or _extract_int_field(html, "shareUk") or _extract_int_field(html, "uk")
            attempts.append(
                {
                    "url": candidate,
                    "final_url": final_url,
                    "status": status,
                    "redirect_location": redirect_location,
                    "has_html": bool(html),
                    "html_size": len(html or ""),
                    "share_id_hint": share_id_hint,
                    "uk_hint": uk_hint,
                }
            )
            if html:
                probe_score = (
                    2 if share_id_hint > 0 else 0,
                    1 if uk_hint > 0 else 0,
                    1 if 200 <= status < 300 else 0,
                    len(html or ""),
                )
                if best_probe is None or probe_score > tuple(best_probe.get("score") or ()):
                    best_probe = {
                        "html": html,
                        "final_url": final_url,
                        "status": status,
                        "resp_headers": dict(resp_headers),
                        "used_url": candidate,
                        "score": probe_score,
                    }
            if share_id_hint > 0 and uk_hint > 0:
                selected_probe = {
                    "html": html,
                    "final_url": final_url,
                    "status": status,
                    "resp_headers": dict(resp_headers),
                    "used_url": candidate,
                }
                break

            # 自分享/APP 口令链接偶发返回 302 空体，或命中非目标页面的 2xx HTML。
            # 这里不要在第一条候选上提前停止，继续探测其他规范化候选（/share/init、/s/1...）。
            if status in redirect_statuses or not html or status >= 400:
                await asyncio.sleep(0.15)

        active_probe = selected_probe or best_probe
        if active_probe is not None:
            html = str(active_probe.get("html", "") or "")
            final_url = str(active_probe.get("final_url", "") or "")
            status = int(active_probe.get("status", 0) or 0)
            used_url = str(active_probe.get("used_url", "") or "")
            probe_headers = active_probe.get("resp_headers")
            resp_headers = dict(probe_headers) if isinstance(probe_headers, dict) else {}

        if status >= 400 or not html:
            raise BaiduApiError(
                f"访问百度网盘失败：HTTP {status or 'unknown'}",
                diagnostics={
                    "stage": "fetch_share_html",
                    "tls": tls_diag,
                    "attempts": attempts,
                    "status": status,
                    "url": used_url or canonical_url,
                    "final_url": final_url,
                    "resp_headers": {k: resp_headers.get(k, "") for k in ("Content-Type", "Server", "Location", "Set-Cookie") if k in resp_headers},
                    "body_head": (html or "")[:200],
                },
            )

        share_id = (
            _extract_int_field(html, "shareid")
            or _extract_int_field(html, "shareId")
            or _extract_int_field(html, "share_id")
        )
        # 注意：百度分享页里 `uk` 可能是当前登录用户，真正的分享者字段通常是 `share_uk`。
        uk = _extract_int_field(html, "share_uk") or _extract_int_field(html, "shareUk") or _extract_int_field(html, "uk")
        sign = _extract_str_field(html, "sign") or _extract_str_field(html, "share_sign") or _extract_str_field(html, "shareSign")
        timestamp = str(
            _extract_int_field(html, "timestamp")
            or _extract_int_field(html, "share_timestamp")
            or _extract_int_field(html, "shareTimestamp")
            or 0
        ).strip()
        bdstoken = _extract_str_field(html, "bdstoken")

        ctx: Dict[str, Any] = {
            "provider": "baidu",
            "client_rev": BAIDU_CLIENT_REV,
            "share_url": final_url or used_url or canonical_url,
            "canonical_url": canonical_url,
            "http_status": status,
            "fetch_attempts": attempts,
            "surl": surl,
            "share_id": share_id,
            "uk": uk,
            "sign": sign,
            "timestamp": timestamp,
            "bdstoken": bdstoken,
            "pwd": pwd,
            "tls": tls_diag,
        }

        if share_id <= 0 or uk <= 0:
            title = ""
            try:
                title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
                title = re.sub(r"\s+", " ", (title_match.group(1) if title_match else "")).strip()
            except Exception:
                title = ""
            if title:
                ctx["html_title"] = title
            lowered = (html or "").lower()
            if "captcha" in lowered or "安全验证" in html or "人机验证" in html:
                raise BaiduApiError(
                    "百度网盘需要安全验证/验证码，请先用浏览器打开该分享链接通过验证后再重试",
                    diagnostics=ctx,
                )
            raise BaiduApiError("百度网盘链接解析失败：未获取 shareid/uk（可能链接失效或页面结构变化）", diagnostics=ctx)

        share_url_for_api = str(ctx.get("share_url") or url).strip() or str(url)
        sekey = str(ctx.get("sekey") or "").strip()

        payload = await _share_list(
            session,
            share_url=share_url_for_api,
            share_id=share_id,
            uk=uk,
            page=1,
            num=200,
            dir_path="",
            sekey=sekey,
            ssl_context=ssl_context,
        )
        errno = int(payload.get("errno", 0) or 0)
        if errno in {9019, -9}:
            access_code = str(pwd or "").strip()
            if not access_code:
                raise BaiduApiError("百度网盘分享需要提取码，请在链接里带上 ?pwd=xxxx 或在页面输入提取码", diagnostics=ctx)

            verify_payload = await _share_verify(
                session,
                share_url=share_url_for_api,
                surl=surl,
                pwd=access_code,
                ssl_context=ssl_context,
            )
            verify_errno = int(verify_payload.get("errno", 0) or 0)
            ctx["verify_errno"] = verify_errno
            if verify_errno != 0:
                verify_msg = str(verify_payload.get("show_msg") or verify_payload.get("err_msg") or "").strip()
                if verify_msg:
                    raise BaiduApiError(f"提取码验证失败：{verify_msg}（errno={verify_errno}）", diagnostics={**ctx, "payload": verify_payload})
                raise BaiduApiError(f"提取码验证失败 errno={verify_errno}", diagnostics={**ctx, "payload": verify_payload})

            # 可能返回 randsk（也可能只在 Set-Cookie 里）。
            randsk = str(verify_payload.get("randsk") or "").strip()
            if not randsk:
                try:
                    cookies_view = session.cookie_jar.filter_cookies(URL("https://pan.baidu.com/"))
                    cookie_item = cookies_view.get("BDCLND")
                    randsk = str(cookie_item.value) if cookie_item else ""
                except Exception:
                    randsk = ""
            if randsk:
                ctx["randsk"] = randsk
                ctx["sekey"] = unquote(randsk)
                sekey = str(ctx.get("sekey") or "").strip()

            payload = await _share_list(
                session,
                share_url=share_url_for_api,
                share_id=share_id,
                uk=uk,
                page=1,
                num=200,
                dir_path="",
                sekey=sekey,
                ssl_context=ssl_context,
            )
            errno = int(payload.get("errno", 0) or 0)

        if errno != 0:
            show_msg = str(payload.get("show_msg") or payload.get("err_msg") or "").strip()
            if show_msg:
                raise BaiduApiError(f"百度网盘解析失败：{show_msg}（errno={errno}）", diagnostics={**ctx, "payload": payload})
            raise BaiduApiError(f"百度网盘解析失败 errno={errno}", diagnostics={**ctx, "payload": payload})

        root_items = payload.get("list") or []
        if not isinstance(root_items, list):
            root_items = []

        from collections import deque

        max_files = 5000
        max_dirs = 800
        per_page = 200
        max_pages = 60

        visited_dirs: set[str] = set()
        seen_file_ids: set[str] = set()
        dir_queue: deque[str] = deque()
        files: List[ResolvedFile] = []

        def consume_item(item: Any) -> None:
            if not isinstance(item, dict):
                return
            file_id = str(item.get("fs_id") or item.get("fid") or "").strip()
            name = str(item.get("server_filename") or item.get("name") or "").strip()
            if not file_id or not name:
                return
            is_folder = _is_truthy_flag(item.get("isdir")) or bool(item.get("is_folder"))
            if is_folder:
                path = str(item.get("path") or "").strip()
                if path:
                    dir_queue.append(path)
                return
            if file_id in seen_file_ids:
                return
            seen_file_ids.add(file_id)
            try:
                size = max(0, int(item.get("size") or 0))
            except Exception:
                size = 0
            path = str(item.get("path") or "").strip()
            folder_path = str(os.path.dirname(path) or "/").strip() if path else ""
            files.append(
                ResolvedFile(
                    file_id=file_id,
                    name=name,
                    size=size,
                    is_folder=False,
                    path=path,
                    folder_path=folder_path,
                )
            )

        for item in root_items:
            consume_item(item)

        while dir_queue and len(files) < max_files and len(visited_dirs) < max_dirs:
            dir_path = str(dir_queue.popleft() or "").strip()
            if not dir_path or dir_path in visited_dirs:
                continue
            visited_dirs.add(dir_path)

            page = 1
            while page <= max_pages and len(files) < max_files:
                payload = await _share_list(
                    session,
                    share_url=share_url_for_api,
                    share_id=share_id,
                    uk=uk,
                    page=page,
                    num=per_page,
                    dir_path=dir_path,
                    sekey=sekey,
                    ssl_context=ssl_context,
                )
                errno = int(payload.get("errno", 0) or 0)
                if errno in {9019, -9}:
                    access_code = str(pwd or "").strip()
                    if not access_code:
                        raise BaiduApiError(
                            "百度网盘分享需要提取码，请在链接里带上 ?pwd=xxxx 或在页面输入提取码",
                            diagnostics={**ctx, "dir": dir_path},
                        )
                    verify_payload = await _share_verify(
                        session,
                        share_url=share_url_for_api,
                        surl=surl,
                        pwd=access_code,
                        ssl_context=ssl_context,
                    )
                    verify_errno = int(verify_payload.get("errno", 0) or 0)
                    ctx["verify_errno"] = verify_errno
                    if verify_errno != 0:
                        verify_msg = str(verify_payload.get("show_msg") or verify_payload.get("err_msg") or "").strip()
                        if verify_msg:
                            raise BaiduApiError(
                                f"提取码验证失败：{verify_msg}（errno={verify_errno}）",
                                diagnostics={**ctx, "payload": verify_payload, "dir": dir_path},
                            )
                        raise BaiduApiError(
                            f"提取码验证失败 errno={verify_errno}",
                            diagnostics={**ctx, "payload": verify_payload, "dir": dir_path},
                        )
                    randsk = str(verify_payload.get("randsk") or "").strip()
                    if not randsk:
                        try:
                            cookies_view = session.cookie_jar.filter_cookies(URL("https://pan.baidu.com/"))
                            cookie_item = cookies_view.get("BDCLND")
                            randsk = str(cookie_item.value) if cookie_item else ""
                        except Exception:
                            randsk = ""
                    if randsk:
                        ctx["randsk"] = randsk
                        ctx["sekey"] = unquote(randsk)
                        sekey = str(ctx.get("sekey") or "").strip()

                    payload = await _share_list(
                        session,
                        share_url=share_url_for_api,
                        share_id=share_id,
                        uk=uk,
                        page=page,
                        num=per_page,
                        dir_path=dir_path,
                        sekey=sekey,
                        ssl_context=ssl_context,
                    )
                    errno = int(payload.get("errno", 0) or 0)

                if errno != 0:
                    show_msg = str(payload.get("show_msg") or payload.get("err_msg") or "").strip()
                    if show_msg:
                        raise BaiduApiError(
                            f"百度网盘解析失败：{show_msg}（errno={errno}）",
                            diagnostics={**ctx, "payload": payload, "dir": dir_path},
                        )
                    raise BaiduApiError(
                        f"百度网盘解析失败 errno={errno}",
                        diagnostics={**ctx, "payload": payload, "dir": dir_path},
                    )

                batch = payload.get("list") or []
                if not isinstance(batch, list):
                    batch = []
                batch = [v for v in batch if isinstance(v, dict)]
                if not batch:
                    break
                for item in batch:
                    consume_item(item)
                if len(batch) < per_page:
                    break
                page += 1

        resolved = ResolvedShare(
            share_code=surl,
            share_id=str(int(share_id)),
            pwd=str(pwd or ""),
            files=files,
        )
        return resolved, ctx


async def fetch_baidu_download_url(cookie: str, ctx: Dict[str, Any], file_id: str) -> str:
    """根据解析上下文获取单文件直链（返回 dlink）。

    说明：
    - 直接调用 /api/sharedownload 在较新版本页面下经常返回加密 token（list 为字符串），无法直接用于 aria2。
    - 为提高稳定性，这里采用“保存到网盘(share/transfer) → 再用 /api/download 获取 dlink”的流程。
    """
    cookie_dict = _parse_cookie_header(cookie)
    if not _has_bduss(cookie_dict):
        raise BaiduApiError("未登录百度网盘（缺少 BDUSS）", diagnostics={"stage": "login", "ctx": ctx})

    share_url = str(ctx.get("share_url", "") or "").strip()
    share_id = int(ctx.get("share_id") or 0)
    uk = int(ctx.get("uk") or 0)
    randsk = str(ctx.get("randsk", "") or "").strip()
    sekey = str(ctx.get("sekey", "") or "").strip()

    if not share_url or share_id <= 0 or uk <= 0:
        raise BaiduApiError("百度网盘下载上下文不完整", diagnostics={"file_id": file_id, "ctx": ctx})

    try:
        share_file_id = int(str(file_id or "").strip())
    except Exception as exc:
        raise BaiduApiError("file_id 无效", diagnostics={"file_id": file_id, "ctx": ctx}) from exc

    timeout = aiohttp.ClientTimeout(total=30)
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    ssl_context, tls_diag = _build_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
        try:
            session.cookie_jar.update_cookies(cookie_dict, response_url=URL("https://pan.baidu.com/"))
        except Exception:
            pass

        # 若已知 sekey/randsk，预置 BDCLND cookie（提取码分享需要）。
        if randsk:
            try:
                session.cookie_jar.update_cookies({"BDCLND": randsk}, response_url=URL("https://pan.baidu.com/"))
            except Exception:
                pass

        async def request_json(
            method: str,
            url: str,
            *,
            params: Optional[Dict[str, Any]] = None,
            data: Optional[Dict[str, Any]] = None,
            headers_override: Optional[Dict[str, str]] = None,
        ) -> Dict[str, Any]:
            merged_headers = dict(headers_override or {})

            async def _request_json_via_urllib() -> Dict[str, Any]:
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
                    request.add_header("User-Agent", str(headers.get("User-Agent", _UA)))
                    request.add_header("Accept", str(headers.get("Accept", "application/json, text/javascript, */*; q=0.01")))
                    effective_cookie = str(cookie or "").strip()
                    if randsk and "BDCLND=" not in effective_cookie:
                        effective_cookie = f"{effective_cookie}; BDCLND={randsk}" if effective_cookie else f"BDCLND={randsk}"
                    if effective_cookie:
                        request.add_header("Cookie", effective_cookie)
                    for key, value in merged_headers.items():
                        request.add_header(str(key), str(value))
                    try:
                        with urllib_request.urlopen(request, timeout=25, context=ssl_context) as resp:
                            status = int(getattr(resp, "status", 200) or 200)
                            text = resp.read().decode("utf-8", errors="ignore")
                    except urllib_error.HTTPError as exc:
                        status = int(getattr(exc, "code", 0) or 0)
                        text = exc.read().decode("utf-8", errors="ignore")
                        raise BaiduApiError(
                            f"百度网盘请求失败：HTTP {status}",
                            diagnostics={
                                "stage": "http",
                                "status": status,
                                "url": request_url,
                                "body_head": text[:200],
                                "transport": "urllib",
                            },
                        ) from exc
                    try:
                        payload = json.loads(text or "{}")
                    except Exception:
                        payload = {}
                    if not isinstance(payload, dict):
                        return {}
                    return payload

                return await asyncio.to_thread(_run)

            try:
                async with session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=merged_headers,
                    allow_redirects=True,
                ) as resp:
                    status = int(resp.status)
                    if status == 405 and "pan.baidu.com" in str(urlparse(url).hostname or "").lower():
                        return await _request_json_via_urllib()
                    if status >= 400:
                        body = await resp.text()
                        raise BaiduApiError(
                            f"百度网盘请求失败：HTTP {status}",
                            diagnostics={"stage": "http", "status": status, "url": str(resp.url), "body_head": body[:200]},
                        )
                    payload = await resp.json(content_type=None)
            except aiohttp.ClientConnectorCertificateError as exc:
                raise BaiduApiError(
                    f"访问百度网盘失败（证书校验失败）：{exc}",
                    diagnostics={"stage": "http", "url": url, "tls": tls_diag},
                ) from exc
            except aiohttp.ClientError as exc:
                raise BaiduApiError(
                    f"访问百度网盘失败：{exc}",
                    diagnostics={"stage": "http", "url": url, "tls": tls_diag},
                ) from exc
            if not isinstance(payload, dict):
                return {}
            return payload

        # 1) 获取 pan 侧 bdstoken，用于 share/transfer
        tpl = await request_json(
            "GET",
            "https://pan.baidu.com/api/gettemplatevariable",
            params={"fields": json.dumps(["bdstoken"], ensure_ascii=False)},
            headers_override={
                "Referer": "https://pan.baidu.com/disk/home",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        tpl_errno = int(tpl.get("errno", 0) or 0)
        if tpl_errno != 0:
            show_msg = str(tpl.get("show_msg") or tpl.get("err_msg") or "").strip()
            raise BaiduApiError(
                f"获取百度网盘参数失败：{show_msg or 'unknown'}（errno={tpl_errno}）",
                diagnostics={"stage": "gettemplatevariable", "file_id": file_id, "ctx": ctx, "payload": tpl},
        )
        tpl_result = tpl.get("result") if isinstance(tpl.get("result"), dict) else {}
        bdstoken = str(tpl_result.get("bdstoken") or "").strip()
        if not bdstoken:
            raise BaiduApiError(
                "获取百度网盘参数失败：缺少 bdstoken",
                diagnostics={"stage": "gettemplatevariable", "file_id": file_id, "ctx": ctx, "payload": tpl},
            )

        # 2) 保存到网盘（share/transfer）
        transfer_cache = ctx.get("_transfer_cache")
        if not isinstance(transfer_cache, dict):
            transfer_cache = {}
            ctx["_transfer_cache"] = transfer_cache

        transfer_cache_meta = ctx.get("_transfer_cache_meta")
        if not isinstance(transfer_cache_meta, dict):
            transfer_cache_meta = {}
            ctx["_transfer_cache_meta"] = transfer_cache_meta
        cached_meta = transfer_cache_meta.get(str(file_id))
        cached_to_path = ""
        if isinstance(cached_meta, dict):
            cached_to_path = str(cached_meta.get("to_path") or "").strip()

        cached_to_fsid = transfer_cache.get(str(file_id))
        to_fs_id: Optional[int] = None
        # 旧版本可能只缓存了 to_fs_id，没有缓存 to_path，会导致后续 filemetas 无法取到路径。
        # 这种情况下直接走一次 transfer 获取最新信息。
        if cached_to_fsid is not None and cached_to_path:
            try:
                to_fs_id = int(str(cached_to_fsid).strip())
            except Exception:
                to_fs_id = None

        if to_fs_id is None:
            transfer_params: Dict[str, Any] = {
                "shareid": str(int(share_id)),
                "from": str(int(uk)),
                "ondup": "newcopy",
                "async": "1",
            }
            if bdstoken:
                transfer_params["bdstoken"] = bdstoken
            if sekey:
                transfer_params["sekey"] = sekey

            transfer_payload = await request_json(
                "POST",
                "https://pan.baidu.com/share/transfer",
                params=transfer_params,
                data={
                    "fsidlist": json.dumps([share_file_id], ensure_ascii=False),
                    "path": "/FreedeckTmp",
                    "type": "1",
                },
                headers_override={
                    "Referer": share_url,
                    "Origin": "https://pan.baidu.com",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            transfer_errno = int(transfer_payload.get("errno", 0) or 0)
            if transfer_errno != 0:
                show_msg = str(transfer_payload.get("show_msg") or transfer_payload.get("err_msg") or "").strip()
                raise BaiduApiError(
                    f"保存到网盘失败：{show_msg or 'unknown'}（errno={transfer_errno}）",
                    diagnostics={"stage": "transfer", "file_id": file_id, "ctx": ctx, "payload": transfer_payload},
                )

            extra = transfer_payload.get("extra") if isinstance(transfer_payload.get("extra"), dict) else {}
            extra_list = extra.get("list") if isinstance(extra.get("list"), list) else []
            to_path = ""
            if extra_list and isinstance(extra_list[0], dict):
                to_path = str(extra_list[0].get("to") or "").strip()
                try:
                    to_fs_id = int(extra_list[0].get("to_fs_id") or 0) or None
                except Exception:
                    to_fs_id = None
            if to_path:
                ctx["_last_transfer_to_path"] = to_path

            task_id = 0
            try:
                task_id = int(transfer_payload.get("task_id") or 0)
            except Exception:
                task_id = 0

            if to_fs_id is None and task_id > 0:
                # 大文件/批量可能异步，轮询 taskquery
                deadline = time.time() + 35
                while time.time() < deadline:
                    await asyncio.sleep(0.8)
                    task_payload = await request_json(
                        "GET",
                        "https://pan.baidu.com/share/taskquery",
                        params={"taskid": str(int(task_id)), **({"bdstoken": bdstoken} if bdstoken else {})},
                        headers_override={"Referer": share_url, "X-Requested-With": "XMLHttpRequest"},
                    )
                    task_errno = int(task_payload.get("errno", 0) or 0)
                    if task_errno != 0:
                        continue
                    task_info = task_payload.get("task_info") if isinstance(task_payload.get("task_info"), dict) else {}
                    status = str(task_info.get("status") or task_payload.get("status") or "").strip().lower()
                    if status in {"running", "pending", "doing", "0", "1"}:
                        continue
                    task_list = task_info.get("list") if isinstance(task_info.get("list"), list) else []
                    if task_list and isinstance(task_list[0], dict):
                        try:
                            to_fs_id = int(task_list[0].get("to_fs_id") or 0) or None
                        except Exception:
                            to_fs_id = None
                    if status in {"success", "succeed", "done", "2"} and to_fs_id is not None:
                        break
                    if status in {"fail", "failed", "error", "-1"}:
                        show_msg = str(task_payload.get("show_msg") or task_payload.get("err_msg") or "").strip()
                        raise BaiduApiError(
                            f"保存到网盘失败：{show_msg or status}",
                            diagnostics={"stage": "taskquery", "file_id": file_id, "ctx": ctx, "payload": task_payload},
                        )

            if to_fs_id is None:
                raise BaiduApiError(
                    "保存到网盘失败：缺少 to_fs_id",
                    diagnostics={"stage": "transfer", "file_id": file_id, "ctx": ctx, "payload": transfer_payload},
                )

            transfer_cache[str(file_id)] = int(to_fs_id)
            transfer_cache_meta = ctx.get("_transfer_cache_meta")
            if not isinstance(transfer_cache_meta, dict):
                transfer_cache_meta = {}
                ctx["_transfer_cache_meta"] = transfer_cache_meta
            transfer_cache_meta[str(file_id)] = {
                "to_fs_id": int(to_fs_id),
                "to_path": str(to_path or ctx.get("_last_transfer_to_path") or "").strip(),
            }

        # 3) 用 filemetas 获取 dlink，并解析出最终下载地址（302 Location）
        # 说明：/api/download 返回的 d.pcs 链接在部分环境会出现 dstime 偏移导致 31360 过期，
        # 改用 /api/filemetas → dlink → 302 Location（baidupcs）更稳定。
        transfer_cache_meta = ctx.get("_transfer_cache_meta")
        if not isinstance(transfer_cache_meta, dict):
            transfer_cache_meta = {}
            ctx["_transfer_cache_meta"] = transfer_cache_meta

        cached_meta = transfer_cache_meta.get(str(file_id))
        to_path = ""
        if isinstance(cached_meta, dict):
            to_path = str(cached_meta.get("to_path") or "").strip()

        if not to_path:
            # transfer 返回 extra.list[0].to 为保存后的完整路径
            # 若没有（极少数异步任务），暂时退回到默认目录下的同名文件。
            # 这里优先尝试从 ctx 中的 last_transfer_to 取值。
            to_path = str(ctx.get("_last_transfer_to_path") or "").strip()

        if not to_path:
            raise BaiduApiError(
                "保存到网盘成功但缺少目标路径（to）",
                diagnostics={"stage": "transfer", "file_id": file_id, "ctx": ctx, "to_fs_id": to_fs_id},
            )

        filemetas_payload = await request_json(
            "GET",
            "https://pan.baidu.com/api/filemetas",
            params={
                "target": json.dumps([to_path], ensure_ascii=False),
                "dlink": "1",
                "web": "5",
                # 不要传 origin=dlna，部分账号会触发 31329 hit illeage dlna
            },
            headers_override={
                "Referer": "https://pan.baidu.com/disk/home",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        fm_errno = int(filemetas_payload.get("errno", 0) or 0)
        if fm_errno != 0:
            show_msg = str(filemetas_payload.get("errmsg") or filemetas_payload.get("show_msg") or "").strip()
            raise BaiduApiError(
                f"获取直链失败：{show_msg or 'unknown'}（errno={fm_errno}）",
                diagnostics={"stage": "filemetas", "file_id": file_id, "ctx": ctx, "payload": filemetas_payload},
            )

        info = filemetas_payload.get("info")
        if not isinstance(info, list) or not info:
            raise BaiduApiError(
                "获取直链失败：filemetas 返回缺少 info",
                diagnostics={"stage": "filemetas", "file_id": file_id, "ctx": ctx, "payload": filemetas_payload},
            )
        first = info[0] if isinstance(info[0], dict) else {}
        dlink = str(first.get("dlink") or "").strip()
        if not dlink:
            raise BaiduApiError(
                "获取直链失败：filemetas dlink 为空",
                diagnostics={"stage": "filemetas", "file_id": file_id, "ctx": ctx, "payload": filemetas_payload},
            )
        if dlink.startswith("http://"):
            dlink = "https://" + dlink[len("http://") :]

        # dlink 需要 cookie 才能返回 302 Location
        try:
            async with session.get(
                dlink,
                allow_redirects=False,
                headers={
                    "User-Agent": "pan.baidu.com",
                    "Cookie": cookie,
                },
            ) as resp:
                status = int(resp.status)
                location = str(resp.headers.get("Location") or "").strip()
                if status in {301, 302, 303, 307, 308} and location:
                    return location
                body = await resp.text(errors="ignore")
                raise BaiduApiError(
                    f"获取直链失败：HTTP {status}",
                    diagnostics={
                        "stage": "dlink_redirect",
                        "file_id": file_id,
                        "status": status,
                        "dlink": dlink[:200],
                        "location": location,
                        "body_head": body[:200],
                    },
                )
        except aiohttp.ClientConnectorCertificateError as exc:
            raise BaiduApiError(
                f"访问百度网盘失败（证书校验失败）：{exc}",
                diagnostics={"stage": "dlink_redirect", "file_id": file_id, "tls": tls_diag},
            ) from exc
        except aiohttp.ClientError as exc:
            raise BaiduApiError(
                f"访问百度网盘失败：{exc}",
                diagnostics={"stage": "dlink_redirect", "file_id": file_id, "tls": tls_diag},
            ) from exc


async def fetch_baidu_home_file_dlink(
    *,
    cookie: str,
    path: str,
    access_token: str = "",
    origin: str = "",
    with_info: bool = False,
) -> Any:
    """对“用户自己网盘中的文件”通过网页登录态获取 dlink（不跟随到最终下载节点）。

    `origin="dlna"` 可复用 AList 的 crack 思路；
    `with_info=True` 时返回 `{"dlink": ..., "info": ..., "path": ..., "origin": ...}`。
    """
    cookie_dict = _parse_cookie_header(cookie)
    normalized_access_token = str(access_token or "").strip()
    if not normalized_access_token and not _has_bduss(cookie_dict):
        raise BaiduApiError("未登录百度网盘（缺少 BDUSS）", diagnostics={"stage": "login"})

    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise BaiduApiError("百度网盘文件路径为空", diagnostics={"stage": "home_filemetas"})

    timeout = aiohttp.ClientTimeout(total=20)
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    ssl_context, tls_diag = _build_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
        try:
            session.cookie_jar.update_cookies(cookie_dict, response_url=URL("https://pan.baidu.com/"))
        except Exception:
            pass

        async def request_json(
            method: str,
            url: str,
            *,
            params: Optional[Dict[str, Any]] = None,
            data: Optional[Dict[str, Any]] = None,
            headers_override: Optional[Dict[str, str]] = None,
        ) -> Dict[str, Any]:
            merged_headers = dict(headers_override or {})

            async def _request_json_via_urllib() -> Dict[str, Any]:
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
                    request.add_header("User-Agent", str(headers.get("User-Agent", _UA)))
                    request.add_header("Accept", str(headers.get("Accept", "application/json, text/javascript, */*; q=0.01")))
                    effective_cookie = str(cookie or "").strip()
                    if effective_cookie:
                        request.add_header("Cookie", effective_cookie)
                    for key, value in merged_headers.items():
                        request.add_header(str(key), str(value))
                    try:
                        with urllib_request.urlopen(request, timeout=25, context=ssl_context) as resp:
                            status = int(getattr(resp, "status", 200) or 200)
                            text = resp.read().decode("utf-8", errors="ignore")
                    except urllib_error.HTTPError as exc:
                        status = int(getattr(exc, "code", 0) or 0)
                        text = exc.read().decode("utf-8", errors="ignore")
                        raise BaiduApiError(
                            f"百度网盘请求失败：HTTP {status}",
                            diagnostics={
                                "stage": "http",
                                "status": status,
                                "url": request_url,
                                "body_head": text[:200],
                                "transport": "urllib",
                            },
                        ) from exc
                    try:
                        payload = json.loads(text or "{}")
                    except Exception:
                        payload = {}
                    return payload if isinstance(payload, dict) else {}

                return await asyncio.to_thread(_run)

            try:
                async with session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=merged_headers,
                    allow_redirects=True,
                ) as resp:
                    status = int(resp.status)
                    if status == 405 and "pan.baidu.com" in str(urlparse(url).hostname or "").lower():
                        return await _request_json_via_urllib()
                    if status >= 400:
                        body = await resp.text()
                        raise BaiduApiError(
                            f"百度网盘请求失败：HTTP {status}",
                            diagnostics={"stage": "http", "status": status, "url": str(resp.url), "body_head": body[:200]},
                        )
                    payload = await resp.json(content_type=None)
            except aiohttp.ClientConnectorCertificateError as exc:
                raise BaiduApiError(
                    f"访问百度网盘失败（证书校验失败）：{exc}",
                    diagnostics={"stage": "http", "url": url, "tls": tls_diag},
                ) from exc
            except aiohttp.ClientError as exc:
                raise BaiduApiError(
                    f"访问百度网盘失败：{exc}",
                    diagnostics={"stage": "http", "url": url, "tls": tls_diag},
                ) from exc
            return payload if isinstance(payload, dict) else {}

        params = {
            "target": json.dumps([normalized_path], ensure_ascii=False),
            "dlink": "1",
            "web": "5",
        }
        if normalized_access_token:
            params["access_token"] = normalized_access_token
        normalized_origin = str(origin or "").strip()
        if normalized_origin:
            params["origin"] = normalized_origin

        filemetas_payload = await request_json(
            "GET",
            "https://pan.baidu.com/api/filemetas",
            params=params,
            headers_override={
                "Referer": "https://pan.baidu.com/disk/home",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        fm_errno = int(filemetas_payload.get("errno", 0) or 0)
        if fm_errno != 0:
            show_msg = str(filemetas_payload.get("errmsg") or filemetas_payload.get("show_msg") or "").strip()
            raise BaiduApiError(
                f"获取网页登录直链失败：{show_msg or 'unknown'}（errno={fm_errno}）",
                diagnostics={"stage": "home_filemetas", "path": normalized_path, "payload": filemetas_payload},
            )
        info = filemetas_payload.get("info")
        if not isinstance(info, list) or not info:
            raise BaiduApiError(
                "获取网页登录直链失败：filemetas 返回缺少 info",
                diagnostics={"stage": "home_filemetas", "path": normalized_path, "payload": filemetas_payload},
            )
        first = info[0] if isinstance(info[0], dict) else {}
        dlink = str(first.get("dlink") or "").strip()
        if not dlink:
            raise BaiduApiError(
                "获取网页登录直链失败：filemetas dlink 为空",
                diagnostics={
                    "stage": "home_filemetas",
                    "path": normalized_path,
                    "access_token": bool(normalized_access_token),
                    "origin": normalized_origin,
                    "payload": filemetas_payload,
                },
            )
        if dlink.startswith("http://"):
            dlink = "https://" + dlink[len("http://") :]
        if with_info:
            return {
                "dlink": dlink,
                "info": dict(first),
                "path": normalized_path,
                "origin": normalized_origin,
            }
        return dlink


async def fetch_baidu_home_file_dlink_alist(
    *,
    cookie: str,
    path: str,
    access_token: str,
) -> Dict[str, Any]:
    """按 AList `crack` 思路获取百度自有网盘文件 dlink。"""
    payload = await fetch_baidu_home_file_dlink(
        cookie=cookie,
        path=path,
        access_token=access_token,
        origin="dlna",
        with_info=True,
    )
    if not isinstance(payload, dict):
        raise BaiduApiError(
            "百度网盘 AList 实验链路返回格式异常",
            diagnostics={"stage": "home_filemetas_alist", "path": str(path or "").strip()},
        )

    dlink = str(payload.get("dlink", "") or "").strip()
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    if not dlink:
        raise BaiduApiError(
            "百度网盘 AList 实验链路未返回 dlink",
            diagnostics={"stage": "home_filemetas_alist", "path": str(path or "").strip(), "payload": payload},
        )

    return {
        "dlink": dlink,
        "info": dict(info),
        "path": str(payload.get("path", path) or path).strip(),
        "origin": "dlna",
        "headers": {
            "User-Agent": "netdisk",
        },
    }


async def fetch_baidu_home_video_dlink_alist(
    *,
    cookie: str,
    path: str,
    file_id: str,
    access_token: str,
) -> Dict[str, Any]:
    """按 AList `crack_video` 思路获取百度自有网盘视频文件 dlink。"""
    normalized_access_token = str(access_token or "").strip()
    normalized_path = str(path or "").strip()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_access_token:
        raise BaiduApiError("百度网盘 AList 视频链路缺少 access_token", diagnostics={"stage": "home_mediainfo_alist"})
    if not normalized_path:
        raise BaiduApiError("百度网盘 AList 视频链路缺少文件路径", diagnostics={"stage": "home_mediainfo_alist"})
    if not normalized_file_id:
        raise BaiduApiError("百度网盘 AList 视频链路缺少文件 ID", diagnostics={"stage": "home_mediainfo_alist"})

    cookie_dict = _parse_cookie_header(cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    ssl_context, tls_diag = _build_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
        try:
            if cookie_dict:
                session.cookie_jar.update_cookies(cookie_dict, response_url=URL("https://pan.baidu.com/"))
        except Exception:
            pass

        params = {
            "access_token": normalized_access_token,
            "type": "VideoURL",
            "path": normalized_path,
            "fs_id": normalized_file_id,
            "devuid": "0%1",
            "clienttype": "1",
            "channel": "android_15_25010PN30C_bd-netdisk_1523a",
            "nom3u8": "1",
            "dlink": "1",
            "media": "1",
            "origin": "dlna",
        }
        try:
            async with session.get(
                "https://pan.baidu.com/api/mediainfo",
                params=params,
                allow_redirects=True,
            ) as resp:
                status = int(resp.status or 0)
                body_text = await resp.text()
                if status >= 400:
                    raise BaiduApiError(
                        f"百度网盘请求失败：HTTP {status}",
                        diagnostics={
                            "stage": "home_mediainfo_alist",
                            "status": status,
                            "url": str(resp.url),
                            "body_head": body_text[:200],
                        },
                    )
                try:
                    payload = json.loads(body_text or "{}")
                except Exception:
                    payload = {}
        except aiohttp.ClientConnectorCertificateError as exc:
            raise BaiduApiError(
                f"访问百度网盘失败（证书校验失败）：{exc}",
                diagnostics={"stage": "home_mediainfo_alist", "path": normalized_path, "tls": tls_diag},
            ) from exc
        except aiohttp.ClientError as exc:
            raise BaiduApiError(
                f"访问百度网盘失败：{exc}",
                diagnostics={"stage": "home_mediainfo_alist", "path": normalized_path, "tls": tls_diag},
            ) from exc

    if not isinstance(payload, dict):
        raise BaiduApiError(
            "百度网盘 AList 视频链路返回格式异常",
            diagnostics={"stage": "home_mediainfo_alist", "path": normalized_path, "payload_type": type(payload).__name__},
        )
    errno = int(payload.get("errno", payload.get("error_code", 0)) or 0)
    if errno not in {0, 31023}:
        show_msg = str(payload.get("errmsg") or payload.get("show_msg") or "").strip()
        raise BaiduApiError(
            f"获取 AList 视频直链失败：{show_msg or 'unknown'}（errno={errno}）",
            diagnostics={"stage": "home_mediainfo_alist", "path": normalized_path, "payload": payload},
        )
    dlink = str(((payload.get("info") or {}) if isinstance(payload.get("info"), dict) else {}).get("dlink") or "").strip()
    if not dlink:
        raise BaiduApiError(
            "百度网盘 AList 视频链路未返回 dlink",
            diagnostics={"stage": "home_mediainfo_alist", "path": normalized_path, "payload": payload},
        )
    return {
        "dlink": dlink,
        "info": payload.get("info") if isinstance(payload.get("info"), dict) else {},
        "path": normalized_path,
        "origin": "dlna",
        "headers": {
            "User-Agent": "netdisk",
        },
    }
