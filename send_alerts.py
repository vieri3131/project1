from datetime import datetime

from api.main import build_matches_for_subscription, get_active_subscriptions, get_supabase
from email_service import send_daily_alert_email


def _safe_alert_logs_select(sb, subscription_id: str):
    try:
        return (
            sb.table("alert_logs")
            .select("listing_id")
            .eq("subscription_id", subscription_id)
            .execute()
        )
    except Exception as e:
        print(f"[alerts] alert_logs table unavailable or query failed: {e}", flush=True)
        return None


def _safe_alert_logs_insert(sb, rows: list[dict]):
    if not rows:
        return
    try:
        sb.table("alert_logs").insert(rows).execute()
    except Exception as e:
        print(f"[alerts] alert_logs insert failed: {e}", flush=True)


def main():
    sb = get_supabase()
    subscribers = get_active_subscriptions()
    print(f"[alerts] active subscribers: {len(subscribers)}", flush=True)
    total_sent = 0

    for sub in subscribers:
        matches = build_matches_for_subscription(sub, limit=20)
        if not matches:
            continue

        existing = _safe_alert_logs_select(sb, sub["id"])
        already_sent_ids = set()
        if existing and getattr(existing, "data", None):
            already_sent_ids = {str(x.get("listing_id")) for x in existing.data if x.get("listing_id")}

        fresh = [m for m in matches if str(m.get("id")) not in already_sent_ids]
        if not fresh:
            continue

        send_daily_alert_email(sub, fresh)
        total_sent += 1

        log_rows = [
            {
                "subscription_id": sub["id"],
                "email": sub["email"],
                "listing_id": str(item.get("id")),
                "sent_at": datetime.utcnow().isoformat(),
            }
            for item in fresh
            if item.get("id")
        ]
        _safe_alert_logs_insert(sb, log_rows)
        print(f"[alerts] sent to {sub['email']} / {len(fresh)} items", flush=True)

    print(f"[alerts] done. emails sent: {total_sent}", flush=True)


if __name__ == "__main__":
    main()
