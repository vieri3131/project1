import hashlib
import hmac
import os
from typing import Iterable

import requests
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
MAIL_FROM = os.getenv("MAIL_FROM")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:3000")
API_PUBLIC_BASE_URL = os.getenv("API_PUBLIC_BASE_URL", "http://localhost:8000")
UNSUBSCRIBE_SECRET = os.getenv("UNSUBSCRIBE_SECRET") or os.getenv("SUPABASE_KEY", "local-dev-secret")


def generate_unsubscribe_token(subscription_id: str, email: str) -> str:
    payload = f"{subscription_id}:{email}".encode("utf-8")
    secret = UNSUBSCRIBE_SECRET.encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_unsubscribe_token(subscription_id: str, email: str, token: str) -> bool:
    expected = generate_unsubscribe_token(subscription_id, email)
    return hmac.compare_digest(expected, token or "")


def _send_resend_email(*, to: list[str], subject: str, html: str) -> None:
    if not RESEND_API_KEY or not MAIL_FROM:
        raise RuntimeError("RESEND_API_KEY or MAIL_FROM is missing")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": MAIL_FROM,
            "to": to,
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Resend send failed: {response.status_code} {response.text[:300]}")


def _fmt_price(value) -> str:
    try:
        return f"{int(float(value)):,}만원"
    except Exception:
        return "-"


def _fmt_discount(value) -> str:
    try:
        return f"{float(value):.1f}%"
    except Exception:
        return "-"


def _build_listing_rows(listings: Iterable[dict]) -> str:
    rows = []
    for item in listings:
        props = item.get("properties") or {}
        rows.append(
            f"""
            <tr>
              <td style=\"padding:10px;border-bottom:1px solid #e5e7eb;\">{props.get('apt_name') or '-'}</td>
              <td style=\"padding:10px;border-bottom:1px solid #e5e7eb;\">{props.get('dong') or item.get('region_name') or '-'}</td>
              <td style=\"padding:10px;border-bottom:1px solid #e5e7eb;\">{_fmt_price(item.get('price'))}</td>
              <td style=\"padding:10px;border-bottom:1px solid #e5e7eb;\">{_fmt_price(item.get('market_avg'))}</td>
              <td style=\"padding:10px;border-bottom:1px solid #e5e7eb;color:#dc2626;font-weight:700;\">{_fmt_discount(item.get('discount_rate'))}</td>
              <td style=\"padding:10px;border-bottom:1px solid #e5e7eb;\">{item.get('grade') or '-'}</td>
            </tr>
            """.strip()
        )
    return "\n".join(rows)


def send_subscription_confirmation_email(subscription: dict) -> None:
    if not RESEND_API_KEY or not MAIL_FROM:
        print("[email] confirmation skipped: RESEND_API_KEY or MAIL_FROM missing", flush=True)
        return

    sid = subscription.get("id")
    email = subscription.get("email")
    token = generate_unsubscribe_token(sid, email)
    unsubscribe_url = f"{API_PUBLIC_BASE_URL}/unsubscribe?sid={sid}&token={token}"

    html = f"""
    <div style=\"font-family:Arial,sans-serif;line-height:1.6;color:#111827;\">
      <h2>급매물 알림 구독이 등록되었습니다.</h2>
      <p>아래 조건으로 새로운 급매물을 매일 확인해 알려드립니다.</p>
      <ul>
        <li><b>이메일</b>: {subscription.get('email')}</li>
        <li><b>지역</b>: {subscription.get('region')}</li>
        <li><b>등급</b>: {subscription.get('grade')}</li>
        <li><b>최소 할인율</b>: {subscription.get('min_discount')}%</li>
      </ul>
      <p><a href=\"{APP_BASE_URL}\">서비스 열기</a></p>
      <p style=\"font-size:12px;color:#6b7280;\">구독 해지: <a href=\"{unsubscribe_url}\">해지 링크</a></p>
    </div>
    """.strip()

    _send_resend_email(to=[email], subject="[급매물 알림] 구독이 등록되었습니다", html=html)


def send_daily_alert_email(subscription: dict, listings: list[dict]) -> None:
    if not listings:
        return
    if not RESEND_API_KEY or not MAIL_FROM:
        print("[email] daily alert skipped: RESEND_API_KEY or MAIL_FROM missing", flush=True)
        return

    sid = subscription.get("id")
    email = subscription.get("email")
    token = generate_unsubscribe_token(sid, email)
    unsubscribe_url = f"{API_PUBLIC_BASE_URL}/unsubscribe?sid={sid}&token={token}"
    rows = _build_listing_rows(listings[:10])

    html = f"""
    <div style=\"font-family:Arial,sans-serif;line-height:1.6;color:#111827;\">
      <h2>새 급매물 {len(listings)}건이 발견되었습니다.</h2>
      <p><b>조건</b>: {subscription.get('region')} / {subscription.get('grade')} / 최소 {subscription.get('min_discount')}%</p>
      <table style=\"border-collapse:collapse;width:100%;font-size:14px;\">
        <thead>
          <tr style=\"background:#f9fafb;\">
            <th style=\"padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;\">단지명</th>
            <th style=\"padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;\">지역</th>
            <th style=\"padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;\">매매가</th>
            <th style=\"padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;\">평균 시세</th>
            <th style=\"padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;\">할인율</th>
            <th style=\"padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;\">등급</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style=\"margin-top:16px;\"><a href=\"{APP_BASE_URL}\">서비스에서 상세 보기</a></p>
      <p style=\"font-size:12px;color:#6b7280;\">구독 해지: <a href=\"{unsubscribe_url}\">해지 링크</a></p>
    </div>
    """.strip()

    _send_resend_email(to=[email], subject=f"[급매물 알림] 새 급매 {len(listings)}건", html=html)
