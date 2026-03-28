import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
MOLIT_API_KEY = os.getenv("MOLIT_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not MOLIT_API_KEY:
    raise RuntimeError("MOLIT_API_KEY is required in environment variables")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required in environment variables")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 핵심 타겟 지역 코드 — 서울 전체(25구) + 경기 핫플레이스(14) + 인천 주요(5) = 44개
# ※ MOLIT API는 구(區)가 있는 시(市)의 경우 시 단위 코드(예: 41130)로 조회 불가 → 구 단위 코드 사용
REGION_CODES = [
    # 서울특별시 (25구 전체)
    "11110", "11140", "11170", "11200", "11215", "11230", "11260", "11290", "11305", "11320",
    "11350", "11380", "11410", "11440", "11470", "11500", "11530", "11545", "11560", "11590",
    "11620", "11650", "11680", "11710", "11740",
    # 경기도 (구 단위 코드 사용 — 구 없는 시는 시 코드)
    "41135",  # 성남시 분당구 (판교, 분당)
    "41290",  # 과천시
    "41450",  # 하남시 (미사, 위례)
    "41210",  # 광명시
    "41117",  # 수원시 영통구 (광교)
    "41463",  # 용인시 기흥구
    "41465",  # 용인시 수지구
    "41590",  # 화성시 (동탄) — 구 없는 시, 시 코드 사용
    "41173",  # 안양시 동안구 (평촌)
    "41310",  # 구리시
    "41285",  # 고양시 일산동구
    "41287",  # 고양시 일산서구
    "41360",  # 남양주시 (다산, 별내)
    "41220",  # 평택시 (고덕신도시)
    # 인천광역시 (주요 신도시)
    "28185",  # 연수구 (송도국제도시)
    "28260",  # 서구 (청라, 검단)
    "28110",  # 중구 (영종하늘도시)
    "28237",  # 부평구
    "28200",  # 남동구 (구월, 논현)
]

def get_year_month_range(months=3):
    result = []
    today = datetime.today()
    seen = set()
    for i in range(months):
        d = today - timedelta(days=30 * i)
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result

def fetch_transactions(region_code, year, month):
    url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    params = {
        "serviceKey": MOLIT_API_KEY,
        "LAWD_CD": region_code,
        "DEAL_YMD": f"{year}{month:02d}",
        "numOfRows": 1000,
        "pageNo": 1,
    }
    res = requests.get(url, params=params, timeout=10)
    try:
        root = ET.fromstring(res.content)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML response for {region_code}/{year}-{month:02d}: {e}") from e
    return root.findall(".//item")

def get(item, tag):
    el = item.find(tag)
    return el.text.strip() if el is not None and el.text and el.text.strip() else None

def parse_rgs_date(val):
    if not val:
        return None
    try:
        parts = val.split(".")
        return f"20{parts[0]}-{parts[1]}-{parts[2]}"
    except (IndexError, ValueError):
        return None

def parse_item(item, region_code):
    year  = get(item, "dealYear")
    month = get(item, "dealMonth")
    day   = get(item, "dealDay")
    deal_date = f"{year}-{int(month):02d}-{int(day):02d}" if year and month and day else None

    cdeal_type = get(item, "cdealType")
    cdeal_day  = get(item, "cdealDay")
    is_cancelled = bool(cdeal_type or cdeal_day)

    cancel_date = None
    if cdeal_day and year and month:
        try:
            cancel_date = f"{year}-{int(month):02d}-{int(cdeal_day):02d}"
        except (ValueError, TypeError):
            cancel_date = None

    apt_seq     = get(item, "aptSeq")
    area_size   = get(item, "excluUseAr")
    deal_amount = get(item, "dealAmount")

    if not apt_seq or not area_size or not deal_amount or not deal_date:
        return None, None

    try:
        area_size_value = float(area_size)
    except ValueError:
        return None, None

    try:
        price_value = int(deal_amount.replace(",", ""))
    except ValueError:
        return None, None

    property_data = {
        "apt_seq":     apt_seq,
        "apt_name":    get(item, "aptNm"),
        "region_code": region_code,
        "dong":        get(item, "umdNm") or get(item, "umdCd"),
        "jibun":       get(item, "jibun"),
        "area_size":   area_size_value,
        "build_year":  int(get(item, "buildYear")) if get(item, "buildYear") else None,
    }

    transaction_data = {
        "apt_seq":           apt_seq,          # 나중에 property_id로 교체용
        "area_size":         area_size_value,  # property 매핑용
        "price":             price_value,
        "deal_date":         deal_date,
        "floor":             int(get(item, "floor")) if get(item, "floor") else None,
        "transaction_type":  get(item, "dealingGbn"),
        "is_cancelled":      is_cancelled,
        "cancel_date":       cancel_date,
        "registration_date": parse_rgs_date(get(item, "rgstDate")),
    }

    return property_data, transaction_data

def batch_upsert_properties(properties):
    """중복 제거 후 배치 upsert → {(apt_seq, area_size): property_id} 맵 반환"""
    seen = {}
    unique = []
    for p in properties:
        key = (p["apt_seq"], p["area_size"])
        if key not in seen:
            seen[key] = True
            unique.append(p)

    # 배치 upsert (한 번에 전송) — ignore_duplicates=True: 이미 존재하는 행은 덮어쓰지 않음
    supabase.table("properties").upsert(
        unique, on_conflict="apt_seq,area_size", ignore_duplicates=True
    ).execute()

    # ID 조회 (apt_seq 목록으로 한 번에)
    apt_seqs = list({p["apt_seq"] for p in unique})
    result = (
        supabase.table("properties")
        .select("id, apt_seq, area_size")
        .in_("apt_seq", apt_seqs)
        .execute()
    )

    id_map = {}
    for row in result.data:
        id_map[(row["apt_seq"], row["area_size"])] = row["id"]
    return id_map

def batch_upsert_transactions(transactions, id_map):
    rows = []
    seen = set()  # 중복 제거용

    for t in transactions:
        key_prop = (t.pop("apt_seq"), t.pop("area_size"))
        property_id = id_map.get(key_prop)
        if not property_id:
            continue
        t["property_id"] = property_id

        # 배치 내 중복 제거 (unique constraint 기준)
        dedup_key = (property_id, t.get("deal_date"), t.get("price"), t.get("floor"))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        rows.append(t)

    BATCH_SIZE = 100
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        supabase.table("transactions").upsert(
            batch, on_conflict="property_id,deal_date,price,floor"
        ).execute()

def main():
    periods = get_year_month_range(months=2)
    total = 0

    for region_code in REGION_CODES:
        for year, month in periods:
            print(f"수집 중: {region_code} / {year}-{month:02d}", end=" ... ")

            try:
                items = fetch_transactions(region_code, year, month)
            except Exception as e:
                print(f"API 오류 스킵: {e}")
                continue

            properties, transactions = [], []
            for item in items:
                p, t = parse_item(item, region_code)
                if p and t:
                    properties.append(p)
                    transactions.append(t)

            if not properties:
                print("데이터 없음")
                continue

            try:
                id_map = batch_upsert_properties(properties)
                batch_upsert_transactions(transactions, id_map)
                total += len(transactions)
                print(f"{len(transactions)}건 저장")
            except Exception as e:
                print(f"저장 오류 스킵: {e}")
                continue

    print(f"\n✅ 완료! 총 {total}건 저장됨")

if __name__ == "__main__":
    main()