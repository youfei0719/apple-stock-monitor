"""HTTP 客户端工厂：统一处理代理与 TLS。

- trust_env=False：httpx 解析某些 no_proxy 条目（如 [::1]）会抛 InvalidURL，
  代理改为显式传入。
- 代理来源：显式 proxy 参数 > PROXY_POOL（仅 Apple 侧用）> https_proxy/http_proxy 环境变量。
- verify 走 SSL_CERT_FILE：沙箱出口代理做 TLS 拦截需其自签 CA；
  该变量不存在时回退为默认系统 CA 校验。
"""

import os

import httpx


def make_client(timeout: float = 15, proxy: str | None = None) -> httpx.Client:
    if proxy is None:
        proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy") or None
    return httpx.Client(
        timeout=timeout,
        proxy=proxy,
        trust_env=False,
        follow_redirects=True,
        verify=os.environ.get("SSL_CERT_FILE", True),
    )
