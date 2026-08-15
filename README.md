# div-tracker — 배당 적립 도우미

관심종목(배당 이력 검증된 ETF·종목 고정 리스트)의 **"자기 5년 배당률 밴드 대비 현재 위치"**로
"오늘 적립하기 좋은 순"을 매일 랭킹. 절대 배당률 정렬의 배당 트랩을 피하는 일드 밴드 방식.

- `watchlist.csv` — 추적 종목 (수정·추가는 이 파일만)
- `fetch_rank.py` — 수집·랭킹 (`--selftest` 로 산식 검증)
- `docs/index.html` + `data.json` — GitHub Pages 표
- Actions: 매일 KST 07:40 자동 갱신

## 설정 (1회)
1. GitHub 새 repo(public) 만들고 이 폴더 통째로 push
2. Settings → Pages → Deploy from branch → main /docs
3. Actions 탭에서 daily-rank 워크플로 Run workflow 1회 (첫 data.json 생성)
4. https://<계정>.github.io/<repo>/ 접속

매수신호 아님 · 세금·환율 미반영 · 개별주는 배당 공시 직접 확인.

## 트러블슈팅
- **Actions에서 429/RateLimit 실패**: 야후가 GitHub IP를 일시 차단한 것 — 스크립트에 슬립·재시도가
  내장돼 있지만 전멸하면 몇 시간 뒤 Run workflow 재실행. 반복되면 로컬 실행 후 커밋도 가능.
- **한국 ETF가 "⚠ 분배금 데이터 없음"**: 야후 커버리지 구멍 — 실제 분배금은 운용사 페이지 확인.
  해당 종목은 순위에서 자동 제외되므로 표를 오염시키진 않음.
- **수집 실패 종목**: 페이지 하단 fails 표시 — 티커 표기(.KS/.KQ) 확인.
