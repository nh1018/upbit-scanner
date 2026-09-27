UPBIT + BINANCE SCANNER V1.3 CLOUD
===================================

목적
- PC를 켜두지 않아도 GitHub Actions가 매시간 자동 스캔합니다.
- Upbit 공개 API의 현재 ticker + 완결 일봉 D1~D5를 직접 수집합니다.
- Binance가 GitHub 실행지역에서 접근 가능하면 해외선행도 계산합니다.
- Binance가 차단돼도 Upbit 스캔은 중단하지 않습니다.

최초 설치
1) GitHub에서 새 PUBLIC repository를 만듭니다. (예: upbit-scanner)
2) 이 ZIP 안의 파일/폴더를 repository 최상위에 그대로 업로드합니다.
   .github 폴더까지 반드시 포함되어야 합니다.
3) GitHub repository > Actions > Upbit Scanner V1.3 Cloud > Run workflow 를 한 번 실행합니다.
4) 실행 완료 후 output/latest_scan.json 과 output/latest_candidates.csv가 생성/갱신됩니다.
5) 이후 매시간 17분경 자동 실행됩니다. GitHub schedule은 부하에 따라 지연될 수 있습니다.

ChatGPT에 줄 주소
PUBLIC 저장소라면 아래 RAW 주소가 고정 조회 주소입니다.
https://raw.githubusercontent.com/<GitHub아이디>/<저장소명>/main/output/latest_scan.json

이 주소를 한 번 알려주면, 이후 '업비트' 요청 때 최신 generated_at_kst를 확인한 뒤 후보를 분석하는 용도로 쓸 수 있습니다.

출력
- output/latest_scan.json : 모바일/ChatGPT용 최신 상위 30개 + 생성시각 + Binance 상태
- output/latest_candidates.csv : 최신 후보 30개
- output/scan_YYYYMMDD_HHMMSS.csv : 해당 실행 전체 스냅샷
- data/scan_history_v13.csv : 전체 실행 누적 이력

주의
- GitHub Actions cron은 정확히 정각 실행을 보장하지 않습니다.
- 공개 저장소를 사용하면 결과/이력도 공개됩니다. 이 스캐너에는 API 키나 개인정보를 넣지 마십시오.
- GitHub가 Binance API를 지역 제한하면 binance_status=UNAVAILABLE로 기록되고 Upbit-only로 계속 실행됩니다.
