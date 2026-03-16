import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
MOLIT_API_KEY = os.getenv("MOLIT_API_KEY")
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# 전국 시군구 코드 (행정안전부 기준)
REGION_CODES = [
    "11110","11140","11170",
]

def get_year_month_range(months=3):
    result = []
    today = datetime.today()
    for i in range(months):
        d = today - timedelta(days=30 * i)
        result.append((d.year, d.month))
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
    root = ET.fromstring(res.content)
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
    except:
        return None

def parse_item(item, region_code):
    year  = get(item, "dealYear")
    month = get(item, "dealMonth")
    day   = get(item, "dealDay")
    deal_date = f"{year}-{int(month):02d}-{int(day):02d}" if year and month and day else None

    cdeal_type = get(item, "cdealType")
    cdeal_day  = get(item, "cdealDay")
    is_cancelled = bool(cdeal_type or cdeal_day)

    apt_seq     = get(item, "aptSeq")
    area_size   = get(item, "excluUseAr")
    deal_amount = get(item, "dealAmount")

    if not apt_seq or not area_size or not deal_amount or not deal_date:
        return None, None

    property_data = {
        "apt_seq":     apt_seq,
        "apt_name":    get(item, "aptNm"),
        "region_code": region_code,
        "dong":        get(item, "umdNm") or get(item, "umdCd"),
        "jibun":       get(item, "jibun"),
        "area_size":   float(area_size),
        "build_year":  int(get(item, "buildYear")) if get(item, "buildYear") else None,
    }

    transaction_data = {
        "apt_seq":           apt_seq,          # 나중에 property_id로 교체용
        "area_size":         float(area_size),  # property 매핑용
        "price":             int(deal_amount.replace(",", "")),
        "deal_date":         deal_date,
        "floor":             int(get(item, "floor")) if get(item, "floor") else None,
        "transaction_type":  get(item, "dealingGbn"),
        "is_cancelled":      is_cancelled,
        "cancel_date":       None,
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

    # 배치 upsert (한 번에 전송)
    supabase.table("properties").upsert(
        unique, on_conflict="apt_seq,area_size"
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

    BATCH_SIZE = 200
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        supabase.table("transactions").upsert(
            batch, on_conflict="property_id,deal_date,price,floor"
        ).execute()

def main():
    periods = get_year_month_range(months=3)
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