# Email + 12개월 평균 시세 적용 안내

## 추가 환경변수
- RESEND_API_KEY
- MAIL_FROM
- APP_BASE_URL
- API_PUBLIC_BASE_URL
- UNSUBSCRIBE_SECRET

## 메일 발송 방식
- 구독 등록 시 확인 메일 발송
- collect.py 이후 send_alerts.py가 활성 구독자에게 신규 급매만 발송

## 평균 시세 방식
- 같은 단지 + 면적 ±10㎡ 기준 최근 12개월 데이터를 월별 평균으로 만든 뒤
- 12개월 고정 분모로 나눠 market_avg 반환
- 거래가 없는 달은 0으로 간주

## SQL 적용
Supabase SQL Editor에서 `SUPABASE_SCHEMA_PATCH.sql` 실행
