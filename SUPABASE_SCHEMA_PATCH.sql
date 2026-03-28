-- subscribers 중복 방지 (활성 상태 기준까지 완전히 막으려면 partial index 권장)
create unique index if not exists subscribers_unique_active_idx
on subscribers (email, region, grade, min_discount, is_active);

-- 발송 로그 테이블
create table if not exists alert_logs (
  id uuid primary key default gen_random_uuid(),
  subscription_id uuid not null,
  email text not null,
  listing_id text not null,
  sent_at timestamptz not null default now()
);

create index if not exists alert_logs_subscription_idx on alert_logs (subscription_id);
create index if not exists alert_logs_listing_idx on alert_logs (listing_id);
create unique index if not exists alert_logs_unique_idx on alert_logs (subscription_id, listing_id);
