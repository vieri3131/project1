import os
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://apt-alert-frontend.vercel.app"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get("/listings")
def get_listings(
    region_code: str = Query(None, description="시군구 코드 (예: 11650)"),
    dong: str = Query(None, description="법정동명 (예: 서초동)"),
    min_area: float = Query(None, description="최소 전용면적 (㎡)"),
    max_area: float = Query(None, description="최대 전용면적 (㎡)"),
    limit: int = Query(50, description="최대 결과 수"),
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
        .limit(limit)
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