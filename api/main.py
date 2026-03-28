import math
import os
import re
from datetime import date
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client, create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "*")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="Apt Alert API", version="0.2.0")

# CORS
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

# 핵심 타겟 지역 코드 매핑 — 서울(25) + 경기(14) + 인천(5) = 44개
# ※ MOLIT API는 구(區)가 있는 시(市)는 구 단위 코드로만 조회 가능
REGION_NAME_TO_CODE = {
    # 서울특별시 (25구 전체)
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
    # 경기도 (구 단위 코드 — 구 없는 시는 시 코드)
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
    # 인천광역시 (주요 신도시)
    "인천 연수구": "28185",
    "인천 서구": "28260",
    "인천 중구": "28110",
    "인천 부평구": "28237",
    "인천 남동구": "28200",
}
CODE_TO_REGION_NAME = {v: k for k, v in REGION_NAME_TO_CODE.items()}
VALID_GRADES = {"초급매", "급매", "저평가", "일반"}

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


def _get_deal_date(t: dict) -> date | None:
    d = t.get("deal_date")
    if d:
        try:
            return date.fromisoformat(str(d)[:10])
        except ValueError:
            return None
    return None


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


def _calc_market_avg(all_trades: list[dict], current: dict) -> float | None:
    today = date.today()
    six_months_ago = _months_ago(today, 6)
    one_year_ago = _months_ago(today, 12)

    props = current.get("properties") or {}
    apt_seq = props.get("apt_seq")
    region_code = props.get("region_code")
    area = _safe_float(props.get("area_size"))
    current_id = current.get("id")

    def valid(t: dict) -> bool:
        if t.get("id") == current_id:
            return False
        if t.get("is_cancelled"):
            return False
        price = _safe_float(t.get("price"))
        if price <= 0:
            return False
        return True

    def get_area(t: dict) -> float:
        return _safe_float((t.get("properties") or {}).get("area_size"))

    def get_apt_seq(t: dict) -> str | None:
        return (t.get("properties") or {}).get("apt_seq")

    def get_region(t: dict) -> str | None:
        return (t.get("properties") or {}).get("region_code")

    def get_date(t: dict):
        d = t.get("deal_date")
        if d:
            try:
                return date.fromisoformat(str(d)[:10])
            except ValueError:
                return None
        return None

    valids = [t for t in all_trades if valid(t)]

    # 1순위: 같은 단지 + 면적 ±5 + 최근 6개월
    pool = [
        t
        for t in valids
        if get_apt_seq(t) == apt_seq
        and abs(get_area(t) - area) <= 5
        and (get_date(t) or date.min) >= six_months_ago
    ]

    # 2순위: 같은 단지 + 면적 ±10 + 최근 12개월
    if len(pool) < 2:
        pool = [
            t
            for t in valids
            if get_apt_seq(t) == apt_seq
            and abs(get_area(t) - area) <= 10
            and (get_date(t) or date.min) >= one_year_ago
        ]

    # 3순위: 같은 지역 + 면적 ±10 + 최근 12개월
    if len(pool) < 2:
        pool = [
            t
            for t in valids
            if get_region(t) == region_code
            and abs(get_area(t) - area) <= 10
            and (get_date(t) or date.min) >= one_year_ago
        ]

    # 4순위: 같은 지역 + 면적 ±15
    if len(pool) < 2:
        pool = [
            t
            for t in valids
            if get_region(t) == region_code
            and abs(get_area(t) - area) <= 15
        ]

    if not pool:
        return None

    avg = sum(_safe_float(t.get("price")) for t in pool) / len(pool)
    return round(avg)


def _classify_grade(discount_rate: float) -> str:
    if discount_rate >= 20:
        return "초급매"
    if discount_rate >= 13:
        return "급매"
    if discount_rate >= 5:
        return "저평가"
    return "일반"


def _calc_risk_score(all_trades: list[dict], current: dict, market_avg: float) -> dict:
    """위험 신호 점수 (0–100). 확인된 사기가 아니라 패턴 기반 경고입니다."""
    props = current.get("properties") or {}
    apt_seq = props.get("apt_seq")
    area = _safe_float(props.get("area_size"))
    current_id = current.get("id")
    today = date.today()
    twelve_months_ago = _months_ago(today, 12)
    six_months_ago = _months_ago(today, 6)

    apt_trades = [
        t for t in all_trades
        if (t.get("properties") or {}).get("apt_seq") == apt_seq
        and t.get("id") != current_id
    ]

    signals = []
    score = 0

    # 1. 취소율 — 최근 12개월 내 해당 단지 거래 중 취소 비율
    recent = [t for t in apt_trades if (_get_deal_date(t) or date.min) >= twelve_months_ago]
    if recent:
        cancel_rate = sum(1 for t in recent if t.get("is_cancelled")) / len(recent)
        if cancel_rate >= 0.4:
            score += 35
            signals.append("취소율 높음")
        elif cancel_rate >= 0.2:
            score += 15
            signals.append("취소율 주의")

    # 2. 등기 지연 — 계약일 대비 등기일 평균 간격
    lags = []
    for t in apt_trades:
        deal_d = t.get("deal_date")
        reg_d = t.get("registration_date")
        if deal_d and reg_d:
            try:
                d1 = date.fromisoformat(str(deal_d)[:10])
                d2 = date.fromisoformat(str(reg_d)[:10])
                lag = (d2 - d1).days
                if 0 <= lag < 3650:
                    lags.append(lag)
            except ValueError:
                pass
    if lags:
        avg_lag = sum(lags) / len(lags)
        if avg_lag > 90:
            score += 25
            signals.append("등기 지연 이상")
        elif avg_lag > 60:
            score += 10
            signals.append("등기 지연 주의")

    # 3. 단기 거래 집중 — 최근 6개월 동일 면적대 거래 빈도
    frequent = [
        t for t in apt_trades
        if abs(_safe_float((t.get("properties") or {}).get("area_size")) - area) <= 5
        and (_get_deal_date(t) or date.min) >= six_months_ago
        and not t.get("is_cancelled")
    ]
    if len(frequent) >= 4:
        score += 20
        signals.append("단기 거래 집중")
    elif len(frequent) >= 3:
        score += 10

    # 4. 시세 초과 가격 — 시세보다 15% 이상 높은 경우
    price = _safe_float(current.get("price"))
    if market_avg > 0 and price > market_avg * 1.15:
        score += 20
        signals.append("시세 초과 가격")

    score = min(100, score)
    level = "위험" if score >= 60 else "주의" if score >= 30 else "낮음"
    return {"score": score, "level": level, "signals": signals}


def _calc_price_trend(all_trades: list[dict], current: dict) -> dict | None:
    """동일 단지+면적 기준 최근 거래 추세 및 3개월 예측 (최소 4건 필요)."""
    props = current.get("properties") or {}
    apt_seq = props.get("apt_seq")
    area = _safe_float(props.get("area_size"))
    current_id = current.get("id")

    relevant = [
        t for t in all_trades
        if (t.get("properties") or {}).get("apt_seq") == apt_seq
        and abs(_safe_float((t.get("properties") or {}).get("area_size")) - area) <= 5
        and not t.get("is_cancelled")
        and t.get("id") != current_id
        and _get_deal_date(t) is not None
        and _safe_float(t.get("price")) > 0
    ]

    if len(relevant) < 4:
        return None

    relevant.sort(key=lambda t: _get_deal_date(t))

    base_date = _get_deal_date(relevant[0])
    points = [
        ((_get_deal_date(t) - base_date).days, _safe_float(t.get("price")))
        for t in relevant
    ]

    n = len(points)
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n

    num = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
    den = sum((p[0] - mean_x) ** 2 for p in points)

    if den == 0 or mean_y == 0:
        return None

    slope = num / den  # 일별 가격 변화량
    trend_rate = round((slope * 30 / mean_y) * 100, 1)  # 월별 변화율(%)

    direction = "상승" if trend_rate >= 0.5 else "하락" if trend_rate <= -0.5 else "보합"

    intercept = mean_y - slope * mean_x
    last_x = points[-1][0]
    forecast_price = max(0, round(intercept + slope * (last_x + 90)))

    return {
        "direction": direction,
        "trend_rate": trend_rate,
        "data_points": n,
        "forecast_3m": forecast_price,
    }


def _enrich(all_trades: list[dict], current: dict) -> dict | None:
    market_avg = _calc_market_avg(all_trades, current)
    if not market_avg:
        return None

    price = _safe_float(current.get("price"))
    if price <= 0:
        return None

    discount_rate = round((1 - price / market_avg) * 100, 1)
    grade = _classify_grade(discount_rate)
    risk = _calc_risk_score(all_trades, current, market_avg)
    trend = _calc_price_trend(all_trades, current)

    normalized = _normalize_trade_row(current)
    return {
        **normalized,
        "market_avg": market_avg,
        "discount_rate": discount_rate,
        "grade": grade,
        "risk": risk,
        "price_trend": trend,
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


def _apply_base_filters(
    query,
    region_code: str | None = None,
    region: str | None = None,
    dong: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
):
    picked_region_code = region_code

    if not picked_region_code and region and region != "전체":
        picked_region_code = REGION_NAME_TO_CODE.get(region)

    if picked_region_code:
        query = query.eq("properties.region_code", picked_region_code)

    # dong은 그대로 동 이름으로도 쓰고,
    # region이 매핑 안 되는 문자열이면 dong처럼 취급
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


def _fetch_raw_trades(
    *,
    region_code: str | None = None,
    region: str | None = None,
    dong: str | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
) -> list[dict]:
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
        .eq("is_cancelled", False)
        .order("deal_date", desc=True)
    )

    query = _apply_base_filters(
        query,
        region_code=region_code,
        region=region,
        dong=dong,
        min_area=min_area,
        max_area=max_area,
    )

    result = query.execute()
    return result.data or []


@app.get("/")
def root():
    return {
        "success": True,
        "message": "Apt Alert API is running.",
        "endpoints": [
            "/listings",
            "/filter",
            "/regions",
            "/subscribers",
            "/subscribe",
        ],
        "supabase_connected": supabase is not None,
    }


@app.get("/health")
def health():
    return {
        "success": True,
        "status": "ok",
        "supabase_connected": supabase is not None,
    }


@app.get("/regions")
def get_regions():
    # DB 조회 없이 설정된 42개 타겟 지역을 항상 반환
    # DB 기반으로 반환하면 데이터가 없는 경기/인천 지역이 드롭다운에서 사라지는 버그 발생
    items = [{"code": "ALL", "name": "전체", "display_order": 0}]
    for i, (name, code) in enumerate(REGION_NAME_TO_CODE.items(), start=1):
        items.append({"code": code, "name": name, "display_order": i})

    return {
        "success": True,
        "data": [item["name"] for item in items],
        "items": items,
        "count": len(items),
    }


@app.get("/listings")
def get_listings(
    region_code: str = Query(None, description="시군구 코드 (예: 11650)"),
    region: str = Query(None, description="구 이름 또는 동 이름 (예: 강남구 / 서초동)"),
    dong: str = Query(None, description="법정동명 (예: 서초동)"),
    min_area: float = Query(None, description="최소 전용면적 (㎡)"),
    max_area: float = Query(None, description="최대 전용면적 (㎡)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=100),
):
    try:
        data = _fetch_raw_trades(
            region_code=region_code,
            region=region,
            dong=dong,
            min_area=min_area,
            max_area=max_area,
        )
        normalized = [_normalize_trade_row(row) for row in data]
        sliced, pagination = _paginate(normalized, page, per_page)

        return {
            "success": True,
            "data": sliced,
            "count": pagination["total"],
            "page": pagination["page"],
            "per_page": pagination["per_page"],
            "total_pages": pagination["total_pages"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"listings query failed: {str(e)}")


@app.get("/filter")
def get_filter(
    region_code: str = Query(None, description="시군구 코드 (예: 11650)"),
    region: str = Query(None, description="구 이름 또는 동 이름 (예: 강남구 / 서초동)"),
    dong: str = Query(None, description="법정동명 (예: 서초동)"),
    min_area: float = Query(None, description="최소 전용면적 (㎡)"),
    max_area: float = Query(None, description="최대 전용면적 (㎡)"),
    min_discount: float = Query(0, description="최소 할인율 (%)"),
    grade: str = Query(None, description="급매 등급 (초급매 / 급매 / 저평가 / 일반)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=100),
):
    try:
        all_trades = _fetch_raw_trades(
            region_code=region_code,
            region=region,
            dong=dong,
            min_area=min_area,
            max_area=max_area,
        )

        enriched = [r for t in all_trades if (r := _enrich(all_trades, t))]

        if min_discount:
            enriched = [t for t in enriched if _safe_float(t.get("discount_rate")) >= min_discount]

        if grade and grade in VALID_GRADES:
            enriched = [t for t in enriched if t.get("grade") == grade]

        enriched.sort(key=lambda t: _safe_float(t.get("discount_rate")), reverse=True)

        sliced, pagination = _paginate(enriched, page, per_page)

        return {
            "success": True,
            "data": sliced,
            "count": pagination["total"],
            "page": pagination["page"],
            "per_page": pagination["per_page"],
            "total_pages": pagination["total_pages"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"filter query failed: {str(e)}")


def _validate_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email or ""))


def _normalize_subscription_payload(payload: dict) -> dict:
    email = str(payload.get("email") or "").strip()
    region = str(payload.get("region") or "전체").strip() or "전체"
    grade = str(payload.get("grade") or "전체").strip() or "전체"

    # 기존 프론트 호환: minDiscount / min_discount 둘 다 받음
    raw_discount = payload.get("min_discount", payload.get("minDiscount", 5))
    min_discount = _safe_float(raw_discount, 5)

    return {
        "email": email,
        "region": region,
        "grade": grade,
        "min_discount": min_discount,
    }


@app.post("/subscribers")
def create_subscription(payload: dict = Body(...)):
    sb = get_supabase()
    data = _normalize_subscription_payload(payload)

    if not _validate_email(data["email"]):
        raise HTTPException(status_code=400, detail="올바른 이메일 주소가 아닙니다.")

    if data["min_discount"] < 0 or data["min_discount"] > 100:
        raise HTTPException(status_code=400, detail="min_discount 값이 올바르지 않습니다.")

    try:
        # 중복 조건이 있으면 기존 것 반환
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
            return {
                "success": True,
                "message": "이미 등록된 구독 조건입니다.",
                "data": existing_rows[0],
            }

        inserted = (
            sb.table("subscribers")
            .insert(
                {
                    "email": data["email"],
                    "region": data["region"],
                    "grade": data["grade"],
                    "min_discount": data["min_discount"],
                    "is_active": True,
                }
            )
            .execute()
        )

        rows = inserted.data or []
        if not rows:
            raise HTTPException(status_code=500, detail="구독 저장에 실패했습니다.")

        return {
            "success": True,
            "message": "구독 조건이 저장되었습니다.",
            "data": rows[0],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"subscription insert failed: {str(e)}")


# 기존 프론트 호환용 alias
@app.post("/subscribe")
def create_subscription_alias(payload: dict = Body(...)):
    return create_subscription(payload)


@app.get("/subscribers")
def get_subscribers(
    email: str = Query(None, description="특정 이메일로 필터링"),
    active_only: bool = Query(True, description="활성 구독만 조회"),
):
    sb = get_supabase()

    try:
        query = sb.table("subscribers").select("*").order("created_at", desc=True)

        if email:
            query = query.eq("email", email)

        if active_only:
            query = query.eq("is_active", True)

        result = query.execute()
        rows = result.data or []

        return {
            "success": True,
            "data": rows,
            "count": len(rows),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"subscribers query failed: {str(e)}")


@app.delete("/subscribers/{subscription_id}")
def deactivate_subscription(subscription_id: str):
    sb = get_supabase()

    try:
        updated = (
            sb.table("subscribers")
            .update({"is_active": False})
            .eq("id", subscription_id)
            .execute()
        )

        rows = updated.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="구독을 찾을 수 없습니다.")

        return {
            "success": True,
            "message": "구독이 비활성화되었습니다.",
            "data": rows[0],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"subscription delete failed: {str(e)}")