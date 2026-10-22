# Windows 읽기 전용 시험 운영 절차

## 전제

이 문서는 설치 절차이며 **이번 작업에서 예약 작업은 등록하지 않았습니다**.
PR #40 Draft를 유지하고 시험 checkout에서만 실행합니다. Production main 병합은 필요 없습니다.
GitHub API GET만 수행하며 `workflow_dispatch`나 시장 데이터 수집 기능은 없습니다.
로그는 `D:/repos/freshness-watchdog-evidence-v1`에 새 파일로 저장하고 기존 로그는 덮어쓰지 않습니다.
A baseline, B evidence, 백업, 운영 출력 경로를 로그 경로로 사용하지 않습니다.

## 인증 및 수동 확인

기존 Git Credential Manager에 저장된 GitHub 인증만 읽습니다. 새 로그인·토큰 생성이나
대화형 입력을 요청하지 않습니다. 계정/자격증명이 없거나 읽지 못하면 exit=2로 중단합니다.
이번 PC에서 기존 계정 `nh1018`의 인증 GET `/user` HTTP200을 확인했습니다.
현재 자격증명의 쓰기 권한을 watchdog가 사용하는 것은 아니며, 추후 운영 권한 최소화는 별도 승인 사항입니다.
환경변수에 기존 read token이 이미 제공되는 환경에서는 `--git-credential` 없이 실행할 수도 있습니다.
토큰 값을 스크립트·명령행·작업 XML·로그에 기록하지 마십시오.

```powershell
Set-Location D:/repos/upbit-scanner-freshness-watchdog
$py = 'C:/Users/13820/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $py -B -m freshness_watchdog --git-credential --include-evidence
```

`NO_ACTION`: 현재 복구 제안 없음. `WOULD_DISPATCH`: 복구가 필요하다는 연구 판단만 출력.
`BLOCKED`: 인증/API/시간/revision/실행 상태 등 증거가 불충분하여 판단 중단.
**어떤 경우도 실제 dispatch를 하지 않습니다.** exit=0도 Production freshness PASS를 보장하는 값은 아닙니다.

## 예약 작업용 읽기 전용 wrapper 예시

승인 후 별도 연구 폴더에 아래 내용을 `.ps1`로 저장할 수 있습니다. 이번에는 자동 등록하지 않습니다.
스케줄 등록 전 수동 1회 실행으로 사용 계정의 GCM 접근, Python/Git 경로, JSON 로그를 확인하십시오.

```powershell
$ErrorActionPreference = 'Stop'
Set-Location D:/repos/upbit-scanner-freshness-watchdog
$py = 'C:/Users/13820/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$logRoot = 'D:/repos/freshness-watchdog-evidence-v1'
if (-not (Test-Path -LiteralPath $logRoot)) {
    New-Item -ItemType Directory -Path $logRoot | Out-Null
}
$body = & $py -B -m freshness_watchdog --git-credential --include-evidence
$code = $LASTEXITCODE
$name = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N') + '.json'
$target = Join-Path $logRoot $name
$stream = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($body -join "`n"))
    $stream.Write($bytes, 0, $bytes.Length)
} finally { $stream.Dispose() }
exit $code
```

ログが作れない場合も停止します。外部環境/transportエラーの詳細はタスク履歴で確認します。
ログJSONは公開データのhash/receiptを含みますがAuthorizationを含みません。原則ローカルに保管し
ファイルサイズ増加は監視します。既存ログの自動削除はこの手順に含めません。

## 作業スケジューラ設定案（未登録）

- 名前: `Upbit Freshness Watchdog READ ONLY`。
- 実行者: 既存GCMを利用できる現在のユーザー。管理者権限は不要です。
- 最初は「ユーザーがログオンしているときのみ」。別アカウントへの資格情報コピーは禁止。
- Action: `powershell.exe -NoProfile -NonInteractive -File <上記wrapperの絶対パス>`。
  作業開始ディレクトリは試験checkoutの絶対パス。
- 5分間隔は試験案。未登録です。新規インスタンスは開始しない(`IgnoreNew`)。
- 最大実行時間3分、タスク失敗の即時自動再起動は無効。実際のCLIは各GET timeout15秒、最大40GET。
  3分中断時は観測JSON未完成となる可能性があるため正常観測として扱いません。
- missed startは次の現在時刻の観測だけを実行し、過去hourを作りません。
- 初回は履歴を有効にして結果0/2、起動重複、PowerShell実行ポリシーを確認。
  ポリシーで拒否された場合は報告し、Bypass/レジストリ変更をしません。
- PCのUTC同期状態を確認。時刻は変更しません。PC終了/絶電/ネットワーク断では試験自体が欠測します。

認証401/403・制限429・timeout・不明状態はその回の観測を止めます。タイマー自体を自動停止する
機能はありません。連続失敗時はログを保全して手動で試験タスクを止め、権限/制限/ネットワークを確認します。
新しい資格情報作成や復旧dispatchへの切替を自動で行いません。

## 観測と検証の読み方

`--include-evidence`の `revision`、`latest_runs`、`sources`、`b_current`、`active_runs`、
`read_receipts`を同じ観測単位で保管します。古い観測は再利用せず毎回新規GETで確認します。
main更新/時刻境界またぎの場合はBLOCKEDであり、次の通常試験時刻まで待ちます。
NO_JOURNALはNO_SIGNALではありません。WOULD_DISPATCHを実行命令に変換しません。

GitHub返却のunknown execution stateとqueued/waiting/in_progressは全体をBLOCKEDにします。
最新runの失敗やpublish未確認は当該戦略の復旧提案を出しません。
現在の実装は特定runとblobの完全な因果結合を保証しないため、自動復旧運用への転用はできません。
Windowsでの試験結果は24時間稼働やLinux検証の代替にもなりません。
