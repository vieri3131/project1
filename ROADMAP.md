# Project Roadmap — Apt Alert

**Total Duration:** 10~15 days | **Cost:** ₩0 | **Commits:** Backend + Frontend

---

## Stage 1 — 환경 준비 & 공통 합의 ✅ DONE (1~2일)

| Task | Team | Status |
|---|---|---|
| GitHub repo & branch strategy (main · feature/a-data · feature/b-filter) | A + B | ✅ Done |
| MOLIT API key registration (data.go.kr) | A | ✅ Done |
| Tailwind CSS folder structure · Vercel connection | B | ❓ Check with B |
| Supabase DB schema & API response format agreement | A + B | ✅ Done |

---

## Stage 2 — 핵심 백엔드 개발 ✅ DONE (4~5일)

| Task | Team | Status |
|---|---|---|
| collect.py — MOLIT XML parsing · Supabase upsert (all 212 regions, 3 months) | A | ✅ Done |
| GET /listings — region · type params · Supabase query | A | ✅ Done |
| 6-month average · discount rate calculation · grade classification | B | ✅ Done (in `lib/filter.js`) |
| GET /filter — discount rate · grade params · sorting | A | ✅ Done (in `api/main.py`, 4-tier fallback logic) |

---

## Stage 3 — 프론트엔드 개발 ⚠️ PARTIALLY DONE (3~4일)

| Task | Team | Status |
|---|---|---|
| /listings API integration · price badge · sorting · loading | A | ❌ Todo |
| Discount rate slider · region selection · email input | B | ✅ Done |
| FilterBar · ListingCard · EmailForm components | B | ✅ Done |
| Discount/grade calculation logic (`lib/filter.js`) | B | ✅ Done |
| Email subscription via Resend | B | ✅ Done |
| Frontend–backend API integration (currently using dummy data fallback) | A + B | ❌ Todo |

---

## Stage 4 — 자동화 & 알림 ⏳ NOT STARTED (1~2일)

| Task | Team | Status |
|---|---|---|
| Daily 6am cron · automatic data collection (`collect.py`) | A | ✅ Done |
| Auto alert on discount detection · 3,000 free alerts/month | B | ❌ Todo |

---

## Stage 5 — 배포 & 마무리 ⏳ NOT STARTED (1~2일)

| Task | Team | Status |
|---|---|---|
| Dockerfile · env vars · self-ping (sleep prevention) | A | ❌ Todo |
| GitHub auto deploy · env var setup | B | ❌ Todo |
| QA testing · README · launch prep | A + B | ❌ Todo |

---

## Current Focus

> **Stage 2 DONE. Stage 3 is partially done (Team B jumped ahead). Now entering Stage 3 completion.**
>
> **Immediate blocker:** Frontend is falling back to dummy data — real listings won't show until the frontend–backend API integration is complete.
>
> **Next tasks:**
> - Team A + B: Wire up frontend to live `/filter` API and remove dummy data fallback
> - Team A: Proceed to Stage 5 — Dockerfile + self-ping
