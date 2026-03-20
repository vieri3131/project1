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

# 전국 시군구 코드 (행정안전부 기준)
REGION_CODES = [
    "11110","11140","11170","11200","11215","11230","11260","11290","11305","11320",
    "11350","11380","11410","11440","11470","11500","11530","11545","11560","11590",
    "11620","11650","11680","11710","11740","21110","21130","21150","21170","21190",
    "21210","21230","22110","22130","22150","22170","22190","22210","22230","23110",
    "23130","23150","23170","23190","23210","23230","23250","23270","23290","23310",
    "23330","24110","24130","24140","24160","24170","24180","24190","24210","24230",
    "24250","24260","24270","24280","24290","25110","25130","25150","25170","25190",
    "25210","25230","25250","26110","26140","26170","26200","26230","26260","26290",
    "26320","26350","26380","26410","26440","26470","26500","26530","26560","27110",
    "27140","27170","27200","27230","27260","27290","27320","27350","28110","28140",
    "28177","28185","28200","28237","28245","28260","29110","29140","29155","29170",
    "29200","30110","30140","30170","30200","30230","31110","31140","31170","31200",
    "31710","32110","32130","32150","32170","32190","32210","32230","32250","32710",
    "32720","33110","33130","33150","33170","33350","33360","33380","33390","33400",
    "33410","34110","34130","34140","34150","34160","34170","34180","34190","34210",
    "34230","34250","34260","34270","34280","35110","35130","35150","35170","35189",
    "35210","35220","35230","35240","35250","35260","35270","35280","36110","36130",
    "36150","36170","36190","36210","36220","36230","36240","36250","36260","36270",
    "36280","37110","37130","37150","37170","37190","37210","37230","37250","37270",
    "37280","37290","37300","37310","37320","38110","38130","38150","38170","38190",
    "38210","38230","38240","38250","38260","38270","38280","38290","38300","38310",
    "39110","39130",
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

    BATCH_SIZE = 100
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        supabase.table("transactions").upsert(
            batch, on_conflict="property_id,deal_date,price,floor"
        ).execute()

def main():
    periods = get_year_month_range(months=6)
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