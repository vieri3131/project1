import os
from datetime import date
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Apt Alert API is running. Use /listings to query data."}

@app.get("/listings")
def get_listings(
    region_code: str = Query(None, description="시군구 코드 (예: 11650)"),
    dong: str = Query(None, description="법정동명 (예: 서초동)"),
    min_area: float = Query(None, description="최소 전용면적 (㎡)"),
    max_area: float = Query(None, description="최대 전용면적 (㎡)"),
):
    # properties + transactions 조인 쿼리
    query = (
        supabase.table("transactions")
        .select("""
            id,
            price,
            deal_date,
            floor,
            transaction_type,
            is_cancelled,
            properties (
                apt_seq,
                apt_name,
                region_code,
                dong,
                area_size,
                build_year
            )
        """)
        .eq("is_cancelled", False)
        .order("deal_date", desc=True)
    )

    # 필터 적용
    if region_code:
        query = query.eq("properties.region_code", region_code)
    if dong:
        query = query.eq("properties.dong", dong)
    if min_area:
        query = query.gte("properties.area_size", min_area)
    if max_area:
        query = query.lte("properties.area_size", max_area)

    result = query.execute()
    data = result.data or []
    return {"data": data, "count": len(data)}


# --- 시세/할인율 계산 헬퍼 ---

def _calc_market_avg(all_trades: list, current: dict) -> float | None:
    today = date.today()

    def months_ago(n):
        m = today.month - n
        y = today.year + m // 12
        m = m % 12 or 12
        return date(y, m, 1)

    six_months_ago = months_ago(6)
    one_year_ago = months_ago(12)

    apt_seq = (current.get("properties") or {}).get("apt_seq")
    region_code = (current.get("properties") or {}).get("region_code")
    area = float((current.get("properties") or {}).get("area_size") or 0)
    current_id = current.get("id")

    def valid(t):
        if t.get("id") == current_id:
            return False
        if t.get("is_cancelled"):
            return False
        price = t.get("price")
        if not price or float(price) <= 0:
            return False
        return True

    def get_area(t):
        return float((t.get("properties") or {}).get("area_size") or 0)

    def get_apt_seq(t):
        return (t.get("properties") or {}).get("apt_seq")

    def get_region(t):
        return (t.get("properties") or {}).get("region_code")

    def get_date(t):
        d = t.get("deal_date")
        if d:
            try:
                return date.fromisoformat(str(d)[:10])
            except ValueError:
                return None
        return None

    valids = [t for t in all_trades if valid(t)]

    # 1순위: 같은 단지 + 면적 ±5 + 최근 6개월
    tier1 = [
        t for t in valids
        if get_apt_seq(t) == apt_seq
        and abs(get_area(t) - area) <= 5
        and (get_date(t) or date.min) >= six_months_ago
    ]
    pool = tier1

    # 2순위: 같은 단지 + 면적 ±10 + 최근 12개월
    if len(pool) < 2:
        pool = [
            t for t in valids
            if get_apt_seq(t) == apt_seq
            and abs(get_area(t) - area) <= 10
            and (get_date(t) or date.min) >= one_year_ago
        ]

    # 3순위: 같은 지역 + 면적 ±10 + 최근 12개월
    if len(pool) < 2:
        pool = [
            t for t in valids
            if get_region(t) == region_code
            and abs(get_area(t) - area) <= 10
            and (get_date(t) or date.min) >= one_year_ago
        ]

    # 4순위: 같은 지역 + 면적 ±15
    if len(pool) < 2:
        pool = [
            t for t in valids
            if get_region(t) == region_code
            and abs(get_area(t) - area) <= 15
        ]

    if not pool:
        return None

    avg = sum(float(t["price"]) for t in pool) / len(pool)
    return round(avg)


def _classify_grade(discount_rate: float) -> str:
    if discount_rate >= 20:
        return "초급매"
    if discount_rate >= 13:
        return "급매"
    if discount_rate >= 5:
        return "저평가"
    return "일반"


def _enrich(all_trades: list, current: dict) -> dict | None:
    market_avg = _calc_market_avg(all_trades, current)
    if not market_avg:
        return None
    price = float(current.get("price") or 0)
    discount_rate = round((1 - price / market_avg) * 100, 1)
    grade = _classify_grade(discount_rate)
    return {**current, "market_avg": market_avg, "discount_rate": discount_rate, "grade": grade}


# --- /filter 엔드포인트 ---

VALID_GRADES = {"초급매", "급매", "저평가", "일반"}

@app.get("/filter")
def get_filter(
    region_code: str = Query(None, description="시군구 코드 (예: 11650)"),
    dong: str = Query(None, description="법정동명 (예: 서초동)"),
    min_area: float = Query(None, description="최소 전용면적 (㎡)"),
    max_area: float = Query(None, description="최대 전용면적 (㎡)"),
    min_discount: float = Query(0, description="최소 할인율 (%) — 기본값 0"),
    grade: str = Query(None, description="급매 등급 (초급매 / 급매 / 저평가 / 일반)"),
):
    query = (
        supabase.table("transactions")
        .select("""
            id,
            price,
            deal_date,
            floor,
            transaction_type,
            is_cancelled,
            properties (
                apt_seq,
                apt_name,
                region_code,
                dong,
                area_size,
                build_year
            )
        """)
        .eq("is_cancelled", False)
        .order("deal_date", desc=True)
    )

    if region_code:
        query = query.eq("properties.region_code", region_code)
    if dong:
        query = query.eq("properties.dong", dong)
    if min_area:
        query = query.gte("properties.area_size", min_area)
    if max_area:
        query = query.lte("properties.area_size", max_area)

    all_trades = query.execute().data or []

    # 할인율·등급 계산
    enriched = [r for t in all_trades if (r := _enrich(all_trades, t))]

    # 필터 적용
    if min_discount:
        enriched = [t for t in enriched if t["discount_rate"] >= min_discount]
    if grade and grade in VALID_GRADES:
        enriched = [t for t in enriched if t["grade"] == grade]

    # 할인율 내림차순 정렬
    enriched.sort(key=lambda t: t["discount_rate"], reverse=True)

    return {"data": enriched, "count": len(enriched)}