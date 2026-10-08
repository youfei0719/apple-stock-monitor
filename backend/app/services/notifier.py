"""通知分发：Bark / 企微 / 钉钉 / 飞书 webhook / 邮件 / 短信（预留）。

每条通知都写入 notifications 表；支持通知链路测试。
"""

import smtplib
from email.mime.text import MIMEText

from app.core.config import get_settings
from app.core.http import make_client
from app.core.logging import get_logger
from app.models.models import Notification

log = get_logger("notifier")

CATEGORY_BUY_PATH = {
    "iphone": "buy-iphone",
    "ipad": "buy-ipad",
    "mac": "buy-mac",
    "watch": "buy-watch",
}


def build_product_link(category: str, part_number: str) -> str:
    """通知直达链接：商品页；兜底购物袋。"""
    path = CATEGORY_BUY_PATH.get((category or "").lower())
    if path and part_number:
        return f"https://www.apple.com.cn/shop/{path}/{part_number}"
    return "https://www.apple.com.cn/shop/bag"


def send_bark(bark_key: str, title: str, body: str, url: str = "") -> None:
    endpoint = f"https://api.day.app/{bark_key}"
    payload = {"title": title, "body": body}
    if url:
        payload["url"] = url
    with make_client(timeout=15) as client:
        resp = client.post(endpoint, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"bark HTTP {resp.status_code}: {resp.text[:200]}")


def send_webhook(platform: str, url: str, title: str, body: str) -> None:
    """企微 / 钉钉 / 飞书群机器人 webhook，统一按文本消息发送。"""
    text = f"{title}\n{body}"
    if platform == "wecom":
        payload = {"msgtype": "text", "text": {"content": text}}
    elif platform == "dingtalk":
        payload = {"msgtype": "text", "text": {"content": text}}
    elif platform == "feishu":
        payload = {"msgtype": "text", "content": {"text": text}}
    else:
        raise ValueError(f"unknown webhook platform: {platform}")
    with make_client(timeout=15) as client:
        resp = client.post(url, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"{platform} webhook HTTP {resp.status_code}: {resp.text[:200]}")


def send_email(to_addr: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.SMTP_HOST:
        raise RuntimeError("SMTP not configured")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_addr
    if settings.SMTP_TLS:
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
    try:
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        server.quit()


def send_sms(to_number: str, body: str) -> None:
    """短信通道：二期启用，此处仅占位。"""
    raise NotImplementedError("SMS provider not integrated yet")


CHANNEL_SENDERS = {
    "bark": send_bark,
    "wecom": None,  # 走 send_webhook
    "dingtalk": None,
    "feishu": None,
    "email": None,
    "sms": send_sms,
}


class Notifier:
    def __init__(self, db):
        self.db = db

    def _record(
        self,
        user_id,
        task_id,
        kind,
        channel,
        target,
        title,
        body,
        link,
        status,
        error=None,
    ) -> Notification:
        n = Notification(
            user_id=user_id,
            task_id=task_id,
            kind=kind,
            channel=channel,
            target=target,
            title=title,
            body=body,
            link=link,
            status=status,
            error=error,
        )
        self.db.add(n)
        self.db.commit()
        return n

    def dispatch(
        self,
        user_id,
        task_id,
        channels: dict,
        title: str,
        body: str,
        link: str,
        kind: str = "stock_alert",
    ) -> list[Notification]:
        """按任务渠道配置逐个发送，每条写库。返回通知记录列表。"""
        out: list[Notification] = []
        jobs: list[tuple[str, str]] = []  # (channel, target)
        if channels.get("bark_key"):
            jobs.append(("bark", channels["bark_key"]))
        for wh in channels.get("webhooks") or []:
            if isinstance(wh, dict) and wh.get("url"):
                jobs.append((wh.get("platform", "wecom"), wh["url"]))
        if channels.get("email"):
            jobs.append(("email", channels["email"]))
        if channels.get("sms_to"):
            jobs.append(("sms", channels["sms_to"]))

        for channel, target in jobs:
            try:
                if channel == "bark":
                    send_bark(target, title, body, link)
                elif channel in ("wecom", "dingtalk", "feishu"):
                    send_webhook(channel, target, title, body + f"\n{link}")
                elif channel == "email":
                    send_email(target, title, f"{body}\n\n{link}")
                elif channel == "sms":
                    send_sms(target, body)
                else:
                    raise ValueError(f"unsupported channel: {channel}")
                log.info("notify_sent", channel=channel, task_id=task_id, kind=kind)
                out.append(
                    self._record(user_id, task_id, kind, channel, target, title, body, link, "sent")
                )
            except Exception as e:
                log.warning("notify_failed", channel=channel, task_id=task_id, error=str(e))
                out.append(
                    self._record(
                        user_id, task_id, kind, channel, target, title, body, link, "failed", str(e)
                    )
                )
        return out

    def test_channel(self, channel: str, target: str, user_id=None) -> Notification:
        """通知链路测试：发一条测试消息并写库。"""
        title = "StockMon 通知链路测试"
        body = "这是一条测试通知，说明该渠道配置可用。"
        link = get_settings().BASE_URL
        if channel == "bark":
            fn = lambda: send_bark(target, title, body, link)  # noqa: E731
        elif channel in ("wecom", "dingtalk", "feishu"):
            fn = lambda: send_webhook(channel, target, title, body)  # noqa: E731
        elif channel == "email":
            fn = lambda: send_email(target, title, body)  # noqa: E731
        elif channel == "sms":
            fn = lambda: send_sms(target, body)  # noqa: E731
        else:
            raise ValueError(f"unsupported channel: {channel}")
        try:
            fn()
            log.info("notify_test_ok", channel=channel)
            return self._record(user_id, None, "test", channel, target, title, body, link, "sent")
        except Exception as e:
            log.warning("notify_test_failed", channel=channel, error=str(e))
            return self._record(
                user_id, None, "test", channel, target, title, body, link, "failed", str(e)
            )
