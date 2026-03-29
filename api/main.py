import json
import math
import os
import re
from datetime import date
from typing import Any

try:
    from google import genai
except Exception:
    genai = None
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from supabase import Client, create_client

from email_service import (
    generate_unsubscribe_token,
    send_daily_alert_email,
    send_subscription_confirmation_email,
    verify_unsubscribe_token,
)

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "*")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

_gemini_client = None
_ai_cache: dict = {}
_AI_CACHE_MAX = 200


def _get_gemini():
    global _gemini_client
    if _gemini_client is None and GEMINI_API_KEY and genai is not None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="Apt Alert API", version="0.3.0")

if CORS_ALLOWED_ORIGINS.strip() == "*":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    origins = [x.strip() for x in CORS_ALLOWED_ORIGINS.split(",") if x.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

REGION_NAME_TO_CODE = {
    "종로구": "11110",
    "중구": "11140",
    "용산구": "11170",
    "성동구": "11200",
    "광진구": "11215",
    "동대문구": "11230",
    "중랑구": "11260",
    "성북구": "11290",
    "강북구": "11305",
    "도봉구": "11320",
    "노원구": "11350",
    "은평구": "11380",
    "서대문구": "11410",
    "마포구": "11440",
    "양천구": "11470",
    "강서구": "11500",
    "구로구": "11530",
    "금천구": "11545",
    "영등포구": "11560",
    "동작구": "11590",
    "관악구": "11620",
    "서초구": "11650",
    "강남구": "11680",
    "송파구": "11710",
    "강동구": "11740",
    "경기 성남 분당구": "41135",
    "경기 과천시": "41290",
    "경기 하남시": "41450",
    "경기 광명시": "41210",
    "경기 수원 영통구": "41117",
    "경기 용인 기흥구": "41463",
    "경기 용인 수지구": "41465",
    "경기 화성시": "41590",
    "경기 안양 동안구": "41173",
    "경기 구리시": "41310",
    "경기 고양 일산동구": "41285",
    "경기 고양 일산서구": "41287",
    "경기 남양주시": "41360",
    "경기 평택시": "41220",
    "인천 연수구": "28185",
    "인천 서구": "28260",
    "인천 중구": "28110",
    "인천 부평구": "28237",
    "인천 남동구": "28200",
}
CODE_TO_REGION_NAME = {v: k for k, v in REGION_NAME_TO_CODE.items()}
VALID_GRADES = {"초급매", "급매", "저평가", "일반"}
MAX_ALL_REGION_FETCH = 500
MAX_ALL_REGION_RECENT_FETCH = 500
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_supabase() -> Client:
    if supabase is None:
        raise HTTPException(
            status_code=500,
            detail="Supabase environment variables are missing. Check SUPABASE_URL / SUPABASE_KEY.",
        )
    return supabase


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _months_ago(today: date, n: int) -> date:
    y = today.year
    m = today.month - n
    while m <= 0:
        y -= 1
        m += 12
    return date(y, m, 1)


def _last_n_month_keys(today: date, n: int) -> list[str]:
    keys = []
    for i in range(n):
        dt = _months_ago(today, i)
        keys.append(f"{dt.year:04d}-{dt.month:02d}")
    return list(reversed(keys))


def _normalize_trade_row(row: dict) -> dict:
    properties = row.get("properties") or {}
    region_code = properties.get("region_code")
    district_name = CODE_TO_REGION_NAME.get(region_code)
    dong_name = properties.get("dong")

    return {
        **row,
        "region_name": district_name or dong_name,
        "dong_name": dong_name,
        "properties": {
            **properties,
            "region_name": district_name or dong_name,
            "dong_name": dong_name,
        },
    }


def _apartment_base_key(props: dict) -> str | None:
    apt_seq = str(props.get("apt_seq") or "").strip()
    if apt_seq:
        return f"seq:{apt_seq}"

    apt_name = str(props.get("apt_name") or "").strip()
    region_code = str(props.get("region_code") or "").strip()
    dong_name = str(props.get("dong") or props.get("dong_name") or "").strip()

    if apt_name and region_code and dong_name:
        return f"name:{apt_name}|rc:{region_code}|dong:{dong_name}"
    if apt_name and region_code:
        return f"name:{apt_name}|rc:{region_code}"
    return None



def _is_all_region_request(*, region_code: str | None = None, region: str | None = None, dong: str | None = None) -> bool:
    picked_region = str(region or "").strip()
    picked_dong = str(dong or "").strip()
    return not region_code and not picked_dong and (not picked_region or picked_region == "전체")


def _fetch_recent_12m_trades(*, region_code: str | None = None, region: str | None = None, dong: str | None = None, min_area: float | None = None, max_area: float | None = None, months: int = 12) -> list[dict]:
    """필터 범위 안의 최근 N개월 거래를 한 번만 조회한다."""
    sb = get_supabase()
    start_date = _months_ago(date.today(), months).isoformat()

    query = (
        sb.table("transactions")
        .select(
            """
            id,
            price,
            deal_date,
            floor,
            transaction_type,
            is_cancelled,
            registration_date,
            properties!inner (
                apt_seq,
                apt_name,
                region_code,
                dong,
                area_size,
                build_year
            )
            """
        )
        .gte("deal_date", start_date)
        .eq("is_cancelled", False)
        .order("deal_date", desc=True)
    )
    query = _apply_base_filters(query, region_code=region_code, region=region, dong=dong, min_area=min_area, max_area=max_area)
    if _is_all_region_request(region_code=region_code, region=region, dong=dong):
        query = query.limit(MAX_ALL_REGION_RECENT_FETCH)
    result = query.execute()
    return result.data or []


def _group_trades_by_apartment(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        props = row.get("properties") or {}
        key = _apartment_base_key(props)
        if not key:
            continue
        groups.setdefault(key, []).append(row)
    return groups


def _calc_market_avg_from_group(current: dict, group: list[dict]) -> tuple[float | None, int]:
    if not group:
        return None, 0

    props = current.get("properties") or {}
    current_id = current.get("id")
    current_area = _safe_float(props.get("area_size"))

    prices: list[float] = []
    for row in group:
        if row.get("id") == current_id:
            continue
        if row.get("is_cancelled"):
            continue

        row_props = row.get("properties") or {}
        row_area = _safe_float(row_props.get("area_size"))

        # 면적 허용오차 제거: 같은 아파트 내에서도 완전 동일 면적만 비교
        if current_area > 0 and row_area > 0 and row_area != current_area:
            continue

        price = _safe_float(row.get("price"))
        if price <= 0:
            continue
        prices.append(price)

    if not prices:
        return None, 0

    avg = sum(prices) / len(prices)
    return round(avg), len(prices)


def _build_market_avg_lookup(rows: list[dict]) -> dict[str, list[dict]]:
    return _group_trades_by_apartment(rows)


def _classify_grade(discount_rate: float) -> str:
    if discount_rate >= 20:
        return "초급매"
    if discount_rate >= 13:
        return "급매"
    if discount_rate >= 5:
        return "저평가"
    return "일반"


def _ai_enrich_risk_trend(items: list[dict], all_trades: list[dict]) -> list[dict]:
    global _ai_cache
    client = _get_gemini()
    if not client or not items:
        return items

    uncached_items = []
    cached_results = {}
    for item in items:
        cache_key = str(item.get("id"))
        if cache_key in _ai_cache:
            cached_results[cache_key] = _ai_cache[cache_key]
        else:
            uncached_items.append(item)

    if not uncached_items:
        return [{**item, **cached_results.get(str(item.get("id")), {})} for item in items]

    contexts = []
    for item in uncached_items:
        props = item.get("properties") or {}
        apt_seq = props.get("apt_seq")
        current_id = item.get("id")
        apt_trades = [
            t for t in all_trades
            if (t.get("properties") or {}).get("apt_seq") == apt_seq and t.get("id") != current_id
        ]
        apt_trades.sort(key=lambda t: t.get("deal_date") or "0000-00-00", reverse=True)
        apt_trades = apt_trades[:12]
        contexts.append({
            "id": current_id,
            "price": int(_safe_float(item.get("price"))),
            "market_avg": int(_safe_float(item.get("market_avg"))),
            "discount_pct": _safe_float(item.get("discount_rate")),
            "area_sqm": _safe_float(props.get("area_size")),
            "recent_trades": [
                {
                    "date": t.get("deal_date"),
                    "price": int(_safe_float(t.get("price"))),
                    "cancelled": bool(t.get("is_cancelled")),
                    "reg_date": t.get("registration_date"),
                }
                for t in apt_trades
            ],
        })

    prompt = f"""한국 아파트 매물 {len(contexts)}개를 분석하세요. 반드시 모든 매물에 대해 risk와 price_trend를 반환하세요. 설명 없이 JSON 배열만 출력하세요.

매물 데이터:
{json.dumps(contexts, ensure_ascii=False)}

【위험도 분석 규칙】
- 취소율: cancelled=true 비율 (40%+ → +35점 \"취소율 높음\", 20%+ → +15점 \"취소율 주의\")
- 등기 지연: deal_date→reg_date 일수 (90일+ → +25점 \"등기 지연 이상\", 60일+ → +10점 \"등기 지연 주의\")
- 단기 거래 집중: 최근 6개월 거래 4건+ → +20점 \"단기 거래 집중\"
- 급격한 가격 하락: discount_pct >= 25 → +20점 \"급격한 가격 하락\"
- 거래 데이터 없음: recent_trades가 0건 → +10점 \"거래 이력 없음\"
- level: score>=60 \"위험\", score>=20 \"주의\", score<20 \"낮음\"

【가격 추세 규칙】
- recent_trades가 3건 이상이면 실제 가격 데이터로 선형 추세 계산
- recent_trades가 1~2건이면 discount_pct 기반으로 추정 (discount_pct>=20 → \"하락\" 추정, discount_pct<10 → \"보합\")
- recent_trades가 0건이면 discount_pct 기반 추정 (큰 할인=하락 압력 의미)
- 반드시 price_trend를 null로 두지 말고 항상 값을 반환할 것
- trend_rate: 월별 가격 변화율(%), direction: trend_rate>=0.5 \"상승\"/\"하락\"/\"보합\"
- forecast_3m: 3개월 후 예상 가격(만원 정수, market_avg 기준으로 추정)

【반환 형식 — JSON 배열만, 마크다운 없이】
[{{\"id\":\"<id>\",\"risk\":{{\"score\":0,\"level\":\"낮음\",\"signals\":[]}},\"price_trend\":{{\"direction\":\"보합\",\"trend_rate\":0.0,\"forecast_3m\":0,\"data_points\":0}}}}]"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-04-17",
            contents=prompt,
        )
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start == -1 or end == 0:
            print(f"[Gemini] No JSON array found in response: {text[:200]}", flush=True)
            return items
        results = json.loads(text[start:end])
        ai_map = {str(r["id"]): r for r in results if "id" in r}

        for item in uncached_items:
            key = str(item.get("id"))
            ai = ai_map.get(key)
            if ai:
                _ai_cache[key] = {k: v for k, v in ai.items() if k in ("risk", "price_trend")}
        if len(_ai_cache) > _AI_CACHE_MAX:
            keys = list(_ai_cache.keys())
            for k in keys[: len(keys) // 2]:
                del _ai_cache[k]

        merged = []
        for item in items:
            key = str(item.get("id"))
            ai = ai_map.get(key) or cached_results.get(key)
            if ai:
                item = {**item}
                if "risk" in ai and ai["risk"]:
                    item["risk"] = ai["risk"]
                if "price_trend" in ai and ai["price_trend"]:
                    item["price_trend"] = ai["price_trend"]
            merged.append(item)
        return merged
    except Exception as e:
        print(f"[Gemini] _ai_enrich_risk_trend failed: {e}", flush=True)
        return items


def _enrich(current: dict, market_avg_lookup: dict[str, list[dict]]) -> dict | None:
    if current.get("is_cancelled"):
        return None

    props = current.get("properties") or {}
    apt_key = _apartment_base_key(props)
    if not apt_key:
        return None

    market_avg, market_avg_count = _calc_market_avg_from_group(current, market_avg_lookup.get(apt_key, []))
    if not market_avg:
        return None

    price = _safe_float(current.get("price"))
    if price <= 0:
        return None

    discount_rate = round((1 - price / market_avg) * 100, 1)
    grade = _classify_grade(discount_rate)
    normalized = _normalize_trade_row(current)
    return {
        **normalized,
        "market_avg": market_avg,
        "market_avg_count": market_avg_count,
        "market_avg_period_months": 12,
        "market_avg_method": "same_apartment_last_12_months",
        "discount_rate": discount_rate,
        "grade": grade,
        "risk": {"score": 0, "level": "낮음", "signals": []},
        "price_trend": None,
    }


def _paginate(items: list[dict], page: int, per_page: int) -> tuple[list[dict], dict]:
    safe_page = max(1, page)
    safe_per_page = max(1, min(100, per_page))
    total = len(items)
    start = (safe_page - 1) * safe_per_page
    end = start + safe_per_page
    sliced = items[start:end]
    return sliced, {
        "page": safe_page,
        "per_page": safe_per_page,
        "total": total,
        "total_pages": max(1, math.ceil(total / safe_per_page)),
    }


def _apply_base_filters(query, region_code: str | None = None, region: str | None = None, dong: str | None = None, min_area: float | None = None, max_area: float | None = None):
    picked_region_code = region_code
    if not picked_region_code and region and region != "전체":
        picked_region_code = REGION_NAME_TO_CODE.get(region)
    if picked_region_code:
        query = query.eq("properties.region_code", picked_region_code)

    picked_dong = dong
    if not picked_dong and region and region != "전체" and region not in REGION_NAME_TO_CODE:
        picked_dong = region
    if picked_dong and picked_dong != "전체":
        query = query.eq("properties.dong", picked_dong)

    if min_area is not None:
        query = query.gte("properties.area_size", min_area)
    if max_area is not None:
        query = query.lte("properties.area_size", max_area)
    return query


def _fetch_raw_trades(*, region_code: str | None = None, region: str | None = None, dong: str | None = None, min_area: float | None = None, max_area: float | None = None) -> list[dict]:
    sb = get_supabase()
    query = (
        sb.table("transactions")
        .select(
            """
            id,
            price,
            deal_date,
            floor,
            transaction_type,
            is_cancelled,
            registration_date,
            properties!inner (
                apt_seq,
                apt_name,
                region_code,
                dong,
                area_size,
                build_year
            )
            """
        )
        .order("deal_date", desc=True)
    )
    query = _apply_base_filters(query, region_code=region_code, region=region, dong=dong, min_area=min_area, max_area=max_area)
    if _is_all_region_request(region_code=region_code, region=region, dong=dong):
        query = query.limit(MAX_ALL_REGION_RECENT_FETCH)
    result = query.execute()
    return result.data or []


def _build_enriched_results(*, region_code: str | None = None, region: str | None = None, dong: str | None = None, min_area: float | None = None, max_area: float | None = None, min_discount: float = 0, grade: str | None = None) -> tuple[list[dict], list[dict]]:
    all_trades = _fetch_raw_trades(region_code=region_code, region=region, dong=dong, min_area=min_area, max_area=max_area)
    recent_12m_trades = _fetch_recent_12m_trades(region_code=region_code, region=region, dong=dong, min_area=min_area, max_area=max_area, months=12)
    market_avg_lookup = _build_market_avg_lookup(recent_12m_trades)

    enriched = [r for t in all_trades if (r := _enrich(t, market_avg_lookup))]
    if min_discount:
        enriched = [t for t in enriched if _safe_float(t.get("discount_rate")) >= min_discount]
    if grade and grade in VALID_GRADES:
        enriched = [t for t in enriched if t.get("grade") == grade]
    enriched.sort(key=lambda t: _safe_float(t.get("discount_rate")), reverse=True)
    return enriched, all_trades


def build_matches_for_subscription(subscription: dict, limit: int | None = None) -> list[dict]:
    region = str(subscription.get("region") or "전체").strip() or "전체"
    grade = str(subscription.get("grade") or "전체").strip() or "전체"
    min_discount = _safe_float(subscription.get("min_discount"), 5)
    grade_value = None if grade == "전체" else grade
    region_value = None if region == "전체" else region
    matches, all_trades = _build_enriched_results(region=region_value, min_discount=min_discount, grade=grade_value)
    if limit is not None:
        matches = matches[:limit]
    return _ai_enrich_risk_trend(matches, all_trades)


def get_active_subscriptions() -> list[dict]:
    sb = get_supabase()
    result = sb.table("subscribers").select("*").eq("is_active", True).order("created_at", desc=False).execute()
    return result.data or []


@app.get("/")
def root():
    return {
        "success": True,
        "message": "Apt Alert API is running.",
        "endpoints": ["/listings", "/filter", "/regions", "/subscribers", "/subscribe", "/unsubscribe"],
        "supabase_connected": supabase is not None,
    }


@app.get("/health")
def health():
    return {"success": True, "status": "ok", "supabase_connected": supabase is not None}


@app.get("/regions")
def get_regions():
    items = [{"code": "ALL", "name": "전체", "display_order": 0}]
    for i, (name, code) in enumerate(REGION_NAME_TO_CODE.items(), start=1):
        items.append({"code": code, "name": name, "display_order": i})
    return {"success": True, "data": [item["name"] for item in items], "items": items, "count": len(items)}


@app.get("/listings")
def get_listings(region_code: str = Query(None), region: str = Query(None), dong: str = Query(None), min_area: float = Query(None), max_area: float = Query(None), page: int = Query(1, ge=1), per_page: int = Query(100, ge=1, le=500)):
    try:
        data = _fetch_raw_trades(region_code=region_code, region=region, dong=dong, min_area=min_area, max_area=max_area)
        normalized = [_normalize_trade_row(row) for row in data]
        sliced, pagination = _paginate(normalized, page, per_page)
        return {"success": True, "data": sliced, "count": pagination["total"], "page": pagination["page"], "per_page": pagination["per_page"], "total_pages": pagination["total_pages"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"listings query failed: {str(e)}")


@app.get("/filter")
def get_filter(region_code: str = Query(None), region: str = Query(None), dong: str = Query(None), min_area: float = Query(None), max_area: float = Query(None), min_discount: float = Query(0), grade: str = Query(None), page: int = Query(1, ge=1), per_page: int = Query(100, ge=1, le=500)):
    try:
        enriched, all_trades = _build_enriched_results(region_code=region_code, region=region, dong=dong, min_area=min_area, max_area=max_area, min_discount=min_discount, grade=grade)
        sliced, pagination = _paginate(enriched, page, per_page)
        sliced = _ai_enrich_risk_trend(sliced, all_trades)
        return {"success": True, "data": sliced, "count": pagination["total"], "page": pagination["page"], "per_page": pagination["per_page"], "total_pages": pagination["total_pages"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"filter query failed: {str(e)}")


def _validate_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email or ""))


def _normalize_subscription_payload(payload: dict) -> dict:
    email = str(payload.get("email") or "").strip()
    region = str(payload.get("region") or "전체").strip() or "전체"
    grade = str(payload.get("grade") or "전체").strip() or "전체"
    raw_discount = payload.get("min_discount", payload.get("minDiscount", 5))
    min_discount = _safe_float(raw_discount, 5)
    return {"email": email, "region": region, "grade": grade, "min_discount": min_discount}


@app.post("/subscribers")
def create_subscription(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    sb = get_supabase()
    data = _normalize_subscription_payload(payload)
    if not _validate_email(data["email"]):
        raise HTTPException(status_code=400, detail="올바른 이메일 주소가 아닙니다.")
    if data["min_discount"] < 0 or data["min_discount"] > 100:
        raise HTTPException(status_code=400, detail="min_discount 값이 올바르지 않습니다.")

    try:
        existing = (
            sb.table("subscribers")
            .select("*")
            .eq("email", data["email"])
            .eq("region", data["region"])
            .eq("grade", data["grade"])
            .eq("min_discount", data["min_discount"])
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        existing_rows = existing.data or []
        if existing_rows:
            return {"success": True, "message": "이미 등록된 구독 조건입니다.", "data": existing_rows[0]}

        inserted = sb.table("subscribers").insert({
            "email": data["email"],
            "region": data["region"],
            "grade": data["grade"],
            "min_discount": data["min_discount"],
            "is_active": True,
        }).execute()

        rows = inserted.data or []
        if not rows:
            raise HTTPException(status_code=500, detail="구독 저장에 실패했습니다.")

        created = rows[0]
        background_tasks.add_task(send_subscription_confirmation_email, created)

        return {"success": True, "message": "구독 조건이 저장되었습니다.", "data": created}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"subscription insert failed: {str(e)}")


@app.post("/subscribe")
def create_subscription_alias(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    return create_subscription(background_tasks, payload)


@app.get("/subscribers")
def get_subscribers(email: str = Query(None), active_only: bool = Query(True)):
    sb = get_supabase()
    try:
        query = sb.table("subscribers").select("*").order("created_at", desc=True)
        if email:
            query = query.eq("email", email)
        if active_only:
            query = query.eq("is_active", True)
        result = query.execute()
        rows = result.data or []
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"subscribers query failed: {str(e)}")


@app.delete("/subscribers/{subscription_id}")
def deactivate_subscription(subscription_id: str):
    sb = get_supabase()
    try:
        updated = sb.table("subscribers").update({"is_active": False}).eq("id", subscription_id).execute()
        rows = updated.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="구독을 찾을 수 없습니다.")
        return {"success": True, "message": "구독이 비활성화되었습니다.", "data": rows[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"subscription delete failed: {str(e)}")


@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_via_token(sid: str = Query(...), token: str = Query(...)):
    sb = get_supabase()
    try:
        result = sb.table("subscribers").select("*").eq("id", sid).limit(1).execute()
        rows = result.data or []
        if not rows:
            return HTMLResponse("<h3>구독 정보를 찾을 수 없습니다.</h3>", status_code=404)
        row = rows[0]
        if not verify_unsubscribe_token(row.get("id"), row.get("email"), token):
            return HTMLResponse("<h3>유효하지 않은 해지 링크입니다.</h3>", status_code=400)

        sb.table("subscribers").update({"is_active": False}).eq("id", sid).execute()
        return HTMLResponse("<h3>구독이 해지되었습니다.</h3><p>이제 더 이상 급매 알림 메일을 보내지 않습니다.</p>")
    except Exception as e:
        return HTMLResponse(f"<h3>해지 처리 중 오류가 발생했습니다.</h3><p>{str(e)}</p>", status_code=500)


@app.get("/subscribers/{subscription_id}/preview-alert")
def preview_subscription_alert(subscription_id: str):
    sb = get_supabase()
    try:
        result = sb.table("subscribers").select("*").eq("id", subscription_id).limit(1).execute()
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="구독 정보를 찾을 수 없습니다.")
        subscription = rows[0]
        matches = build_matches_for_subscription(subscription, limit=10)
        return {"success": True, "subscription": subscription, "matches": matches, "count": len(matches)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"preview failed: {str(e)}")
