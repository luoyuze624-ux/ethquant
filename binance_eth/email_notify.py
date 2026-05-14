"""QQ 邮箱等 SMTP 交易信号推送（需环境变量配置，见 config 模块说明）。"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from binance_eth.log import get_logger

log = get_logger(__name__)


def _parse_receivers(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def is_trade_email_configured(
    smtp_user: str,
    smtp_password: str,
    email_to: str,
) -> bool:
    return bool(smtp_user and smtp_password and _parse_receivers(email_to))


def send_trade_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    email_to: str,
    subject: str,
    body: str,
) -> None:
    recipients = _parse_receivers(email_to)
    if not recipients:
        raise ValueError("收件人为空")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    context = ssl.create_default_context()
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)

    log.info("交易信号邮件已发送: %s", subject)
