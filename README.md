# Upbit/Binance 자동매매 봇 (1H 추세 + 5m 눌림)

> **주의**: 이 코드는 교육/연구 목적 예시입니다. 실거래 손실 책임은 사용자에게 있습니다.

## 전략 개요
- 상위 추세: 1시간봉 EMA(50/200)
- 진입 타이밍: 5분봉 EMA(20/50), RSI(14)
- 방향성:
  - `LONG_ONLY`: 상승 추세 눌림 롱만
  - `BOTH`: 상승 추세 롱 + 하락 추세 숏
- 리스크:
  - 1회 손실 한도(기본 1%)
  - 일일 손실 한도(기본 2%)
  - 연속 손절 2회 시 당일 중지

## 지원 거래소
- Binance USD-M Futures (`BTC/USDT:USDT`)
- Upbit Spot (`BTC/KRW`)
  - 업비트는 현물이라 숏 불가

## 빠른 시작
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env에 API 키 입력 후
python bot.py
```

## 주요 환경변수
- `EXCHANGE`: `binance` 또는 `upbit`
- `SYMBOL`: 바이낸스 `BTC/USDT:USDT`, 업비트 `BTC/KRW`
- `DRY_RUN`: `true`면 주문 미체결(로그만)
- `MODE`: `LONG_ONLY` 또는 `BOTH`
- `RISK_PER_TRADE`: 거래당 리스크 (예: `0.01` = 1%)
- `DAILY_MAX_LOSS`: 일일 최대 손실률 (예: `0.02` = 2%)
- `MAX_TRADES_PER_DAY`: 일일 최대 거래 횟수

## 동작 방식
1. 5분마다 루프 실행
2. 1시간봉/5분봉 데이터 조회
3. 추세/진입 시그널 점수화
4. 포지션 없으면 신규 진입
5. 포지션 있으면 손절/익절/브레이크이븐 관리

## 안전장치
- 기본 `DRY_RUN=true`
- 포지션 진입 시 손절가 필수 계산
- 레버리지(바이낸스) 기본 3배 제한
- 당일 손실 제한 및 연속 손절 제한

