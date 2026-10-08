"""Apple 库存查询客户端：provider 可切换（pickup-message 优先，fulfillment-messages 备用）。

可靠性铁律（2026-10-09 实测 + 调研结论）：
- 三态：available / unavailable / unknown；请求失败一律 unknown，绝不能当无货。
- pickupDisplay == "available" 且 storePickEligible == true 才算可自提。
- HTTP 541/403 = 按 IP 限流，抛 AppleRateLimitError，由引擎做指数退避 + 持久化冷却。
- 同轮请求按（城市锚点 + parts.N + 多 store 参数）合并，绝不逐项请求。
- 请求加随机抖动 UA。
"""

import itertools
import random
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("apple_client")

BASE = "https://www.apple.com.cn"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
]

STATES = ("available", "unavailable", "unknown")


class AppleError(Exception):
    pass


class AppleRateLimitError(AppleError):
    """HTTP 541/403：出口 IP 被限流。"""


@dataclass
class StockResult:
    store_number: str
    store_name: str
    part_number: str
    state: str  # available | unavailable | unknown
    pickup_display: str | None = None
    store_pick_eligible: bool | None = None
    pickup_search_quote: str | None = None


@dataclass
class ProviderResult:
    results: list[StockResult] = field(default_factory=list)
    ok: bool = True
    error: str | None = None


def classify(pickup_display: str | None, store_pick_eligible: bool | None) -> str:
    """统一三态判定。

    R4-P2：缺字段判 unknown 而非 unavailable（此前 pickup_display="available"
    但 store_pick_eligible 缺失时会被判 unavailable，误导为"无货"）。
    """
    if pickup_display == "available" and store_pick_eligible:
        return "available"
    if pickup_display == "unavailable":
        return "unavailable"
    return "unknown"


class BaseProvider:
    name = "base"

    def __init__(self, timeout: int):
        self.timeout = timeout
        self._proxy_cycle = None

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.apple.com.cn/shop/",
        }

    def _proxy(self) -> str | None:
        # 代理池轮换；未配置时返回 None，由 make_client 走系统代理环境变量
        proxies = get_settings().proxy_list
        if not proxies:
            return None
        if self._proxy_cycle is None:
            self._proxy_cycle = itertools.cycle(proxies)
        return next(self._proxy_cycle)

    def _client(self) -> httpx.Client:
        from app.core.http import make_client

        return make_client(timeout=self.timeout, proxy=self._proxy())

    def _http_get(
        self, client: httpx.Client, url: str, params: list[tuple[str, str]]
    ) -> httpx.Response:
        """GET 请求；传输层异常翻译为 AppleError。

        R16-P2-1：httpx.RequestError（ConnectError/TimeoutException 等）必须收敛到
        AppleError 体系——引擎 `_poll_group` 只接 AppleError（标 unknown 并继续其他
        分组），门店目录刷新 `_do_refresh_stores` 的全失败分支只清 REFRESH_AT_KEY
        （原样上抛会锁住管理员 1 小时）。注意：不要归入 AppleRateLimitError，
        传输失败不应触发指数退避冷却。
        """
        try:
            return client.get(url, params=params, headers=self._headers())
        except httpx.RequestError as e:
            raise AppleError(
                f"{self.name} transport error ({type(e).__name__}): {e}"
            ) from e

    def _parse_stores(self, data: dict, want_parts: list[str]) -> list[StockResult]:
        results: list[StockResult] = []
        body = data.get("body") or {}
        for store in body.get("stores", []) or []:
            number = str(store.get("storeNumber", ""))
            name = str(store.get("storeName", ""))
            avail = store.get("partsAvailability") or {}
            for part in want_parts:
                info = avail.get(part) or {}
                pd = info.get("pickupDisplay")
                eligible = info.get("storePickEligible")
                results.append(
                    StockResult(
                        store_number=number,
                        store_name=name,
                        part_number=part,
                        state=classify(pd, eligible),
                        pickup_display=pd,
                        store_pick_eligible=bool(eligible) if eligible is not None else None,
                        pickup_search_quote=info.get("pickupSearchQuote"),
                    )
                )
        return results

    def query(self, parts: list[str], store_numbers: list[str]) -> ProviderResult:
        raise NotImplementedError

    def discover_stores(self, location: str) -> list[dict]:
        """按城市锚点发现附近门店（用于门店目录在线刷新）。"""
        raise NotImplementedError


class PickupMessageProvider(BaseProvider):
    """现行接口：GET /shop/retail/pickup-message（2026-10-09 实测可用）。"""

    name = "pickup-message"

    def _get(self, params: list[tuple[str, str]]) -> dict:
        with self._client() as client:
            resp = self._http_get(client, f"{BASE}/shop/retail/pickup-message", params)
        if resp.status_code in (403, 541):
            raise AppleRateLimitError(f"HTTP {resp.status_code} from {self.name}")
        if resp.status_code != 200:
            raise AppleError(f"{self.name} HTTP {resp.status_code}")
        try:
            return resp.json()
        except Exception as e:
            raise AppleError(f"{self.name} invalid JSON: {e}") from e

    def query(self, parts: list[str], store_numbers: list[str]) -> ProviderResult:
        params: list[tuple[str, str]] = [("pl", "true"), ("searchNearby", "true")]
        for i, p in enumerate(parts):
            params.append((f"parts.{i}", p))
        for s in store_numbers:
            params.append(("store", s))
        data = self._get(params)
        return ProviderResult(results=self._parse_stores(data, parts))

    def discover_stores(self, location: str) -> list[dict]:
        params: list[tuple[str, str]] = [("pl", "true"), ("location", location)]
        data = self._get(params)
        out = []
        for store in (data.get("body") or {}).get("stores", []) or []:
            out.append(
                {
                    "number": str(store.get("storeNumber", "")),
                    "name": str(store.get("storeName", "")),
                    "city": str(store.get("city", "") or ""),
                    "address": str(store.get("address", "") or ""),
                }
            )
        return out


class FulfillmentMessagesProvider(BaseProvider):
    """老接口备用：中国区长期 541（2026-10-09 实测），仅作兜底。"""

    name = "fulfillment-messages"

    def query(self, parts: list[str], store_numbers: list[str]) -> ProviderResult:
        params: list[tuple[str, str]] = [("pl", "true")]
        for i, p in enumerate(parts):
            params.append((f"parts.{i}", p))
        for s in store_numbers:
            params.append(("store", s))
        with self._client() as client:
            resp = self._http_get(client, f"{BASE}/shop/fulfillment-messages", params)
        if resp.status_code in (403, 541):
            raise AppleRateLimitError(f"HTTP {resp.status_code} from {self.name}")
        if resp.status_code != 200:
            raise AppleError(f"{self.name} HTTP {resp.status_code}")
        try:
            data = resp.json()
        except Exception as e:
            raise AppleError(f"{self.name} invalid JSON: {e}") from e
        return ProviderResult(results=self._parse_stores(data, parts))

    def discover_stores(self, location: str) -> list[dict]:
        raise AppleError("fulfillment-messages does not support store discovery")


PROVIDERS: dict[str, type[BaseProvider]] = {
    "pickup-message": PickupMessageProvider,
    "fulfillment-messages": FulfillmentMessagesProvider,
}


class AppleClient:
    """按配置顺序尝试 provider；限流直接上抛（引擎统一退避），其他错误换下一个。"""

    def __init__(self):
        self.settings = get_settings()
        self.providers = [
            PROVIDERS[name](timeout=self.settings.APPLE_TIMEOUT_SEC)
            for name in self.settings.provider_order
            if name in PROVIDERS
        ] or [PickupMessageProvider(timeout=self.settings.APPLE_TIMEOUT_SEC)]

    @property
    def primary(self) -> BaseProvider:
        return self.providers[0]

    def query(self, parts: list[str], store_numbers: list[str]) -> ProviderResult:
        last_err: Exception | None = None
        for provider in self.providers:
            try:
                log.info(
                    "apple_query",
                    provider=provider.name,
                    parts=len(parts),
                    stores=len(store_numbers),
                )
                return provider.query(parts, store_numbers)
            except AppleRateLimitError:
                log.warning("apple_rate_limited", provider=provider.name)
                raise
            except AppleError as e:
                log.warning("apple_provider_failed", provider=provider.name, error=str(e))
                last_err = e
                continue
        raise AppleError(f"all providers failed: {last_err}")

    def discover_stores(self, location: str) -> list[dict]:
        last_err: Exception | None = None
        for provider in self.providers:
            try:
                return provider.discover_stores(location)
            except AppleRateLimitError:
                raise
            except AppleError as e:
                last_err = e
                continue
        raise AppleError(f"all providers failed: {last_err}")
