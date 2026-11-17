"""百度网盘 —— OpenAPI CloudProvider 实现。"""

from __future__ import annotations

from cloud.models import (
    CloudProviderId, CloudCredential, ShareInfo, DirectLink, LoginState,
)

from .openapi import (
    build_baidu_oauth_authorize_url,
    exchange_baidu_oauth_code,
    refresh_baidu_oauth_token,
    fetch_baidu_openapi_user_info,
    resolve_baidu_openapi_share_url as _openapi_resolve_share,
    fetch_baidu_openapi_download_url as _openapi_fetch_download_url,
    is_baidu_openapi_auth_error,
    FREEDECK_BAIDU_APP_KEY,
    FREEDECK_BAIDU_SECRET_KEY,
)
from .client import resolve_baidu_share as _web_resolve_share


class BaiduOpenApiProvider:
    """百度网盘 OpenAPI CloudProvider."""

    @property
    def provider_id(self) -> CloudProviderId:
        return CloudProviderId.BAIDU

    # ── 登录 ──────────────────────────────

    async def check_login(self, credential: CloudCredential) -> LoginState:
        if not credential.access_token:
            return LoginState(logged_in=False, account_name="", message="未授权")
        try:
            info = await fetch_baidu_openapi_user_info(access_token=credential.access_token)
            return LoginState(
                logged_in=True,
                account_name=str(info.get("baidu_name", "") or info.get("netdisk_name", "")),
                message="已授权",
            )
        except Exception as e:
            return LoginState(logged_in=False, account_name="", message=str(e))

    async def refresh_credential(self, credential: CloudCredential) -> CloudCredential:
        token_data = await refresh_baidu_oauth_token(
            app_key=credential.raw_extra.get("app_key", FREEDECK_BAIDU_APP_KEY) or FREEDECK_BAIDU_APP_KEY,
            secret_key=credential.raw_extra.get("secret_key", FREEDECK_BAIDU_SECRET_KEY) or FREEDECK_BAIDU_SECRET_KEY,
            refresh_token=credential.refresh_token,
        )
        credential.access_token = token_data.get("access_token", "")
        credential.refresh_token = token_data.get("refresh_token", credential.refresh_token)
        return credential

    # ── 分享 ──────────────────────────────

    def parse_share_url(self, url: str) -> tuple[str, str]:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(str(url or ""))
        surl = ""
        if "pan.baidu.com" in (parsed.netloc or ""):
            path = parsed.path or ""
            surl = path.split("/s/1")[-1] if "/s/1" in path else path.strip("/")
        pwd = parse_qs(parsed.query or "").get("pwd", [""])[0]
        return surl, pwd

    async def resolve_share(self, url: str, credential: CloudCredential, *,
                            access_code: str = "", root_dir_id: str = "") -> ShareInfo:
        # 优先用 OpenAPI 解析
        try:
            raw_share = await _openapi_resolve_share(
                share_url=url,
                access_token=credential.access_token,
            )
            if raw_share and isinstance(raw_share, tuple):
                share_info, ctx = raw_share
                files = []
                for f in share_info.files if hasattr(share_info, 'files') else []:
                    if hasattr(f, 'name'):
                        files.append(ShareInfo.create_file(
                            file_id=str(getattr(f, 'file_id', '')),
                            name=str(getattr(f, 'name', '')),
                            size_bytes=int(getattr(f, 'size_bytes', 0) or 0),
                            is_directory=False,
                        ))
                return ShareInfo(
                    provider=CloudProviderId.BAIDU,
                    share_code="",
                    share_id=str(ctx.get("share_id", "")),
                    access_code="",
                    title="",
                    files=files,
                )
        except Exception:
            pass

        # 回退 Web API
        cookie = credential.cookie or ""
        raw_share, ctx = await _web_resolve_share(url, cookie)
        files = []
        for f in raw_share.files if hasattr(raw_share, 'files') else []:
            if hasattr(f, 'name'):
                files.append(ShareInfo.create_file(
                    file_id=str(getattr(f, 'file_id', '')),
                    name=str(getattr(f, 'name', '')),
                    size_bytes=int(getattr(f, 'size_bytes', 0) or 0),
                    is_directory=False,
                ))
        return ShareInfo(
            provider=CloudProviderId.BAIDU,
            share_code="",
            share_id=str(ctx.get("share_id", "")),
            access_code="",
            title="",
            files=files,
        )

    async def fetch_direct_link(self, file_id: str, share_id: str,
                                credential: CloudCredential) -> DirectLink:
        dlink = await _openapi_fetch_download_url(
            access_token=credential.access_token,
            share_ctx={"share_id": share_id},
            file_id=file_id,
        )
        return DirectLink(url=dlink, expires_at=0)

    async def refresh_direct_link(self, file_id: str, share_id: str,
                                   credential: CloudCredential) -> DirectLink:
        return await self.fetch_direct_link(file_id, share_id, credential)
