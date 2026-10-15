# pdf-merge-split 작업 규칙

PRD: [PRD_pdf-merge-split_v1.0.md](PRD_pdf-merge-split_v1.0.md). 실제로 있었던 문제와 원인: [docs/LESSONS.md](docs/LESSONS.md).
**문제가 생기면 고친 뒤 반드시 docs/LESSONS.md에 증상·원인·해결·재발 방지를 추가한다.** 시리즈 규칙은 `~/.claude/skills/chaekgalpi-tool`.

## 사용자 파일 안전
- 사용자의 실제 PDF·폴더에는 절대 실행하지 않는다. `samples/`와 `tests/fixtures.py`가 만든 PDF로만 시험한다.
- 원본은 바꾸지 않는다(시험은 전후 SHA-1 비교). 결과는 원본 옆 `_merged`/`_small`, 이름이 있으면 번호. `.part`에 쓴 뒤 `os.replace`.
- 사용자 설정(`settings.json`, exe 옆)을 시험·캡처에서 건드리지 않는다: `PDF_MERGE_SPLIT_SETTINGS` 환경 변수로 임시 파일을 가리킨다(conftest·shots.py·exe 점검 모두).

## 운영 서버(chaekgalpi.co.kr) 차단
- 시험·캡처·exe 점검은 운영 서버에 새 버전 확인 요청을 보내면 안 된다(10-15 사진 정리 511건 사고). `tests/conftest.py`의 autouse 가드가 `urllib.request.urlopen`·`fetch.__kwdefaults__["opener"]`·`check_async.__kwdefaults__["fetcher"]`를 바꾸고, 요청이 기록되면 그 시험을 실패시킨다. 기본 인자로 묶인 함수는 모듈 속성 패치로 안 막힌다.
- exe는 다른 프로세스라 가드가 닿지 않는다: `tests/exe_check.py`처럼 죽은 프록시(`HTTPS_PROXY=http://127.0.0.1:9`) 환경으로 띄운다. 실서버 호출은 릴리스 점검 1회(`tests/exe_check.py --online`)뿐.

## 코드 규칙
- 작업 스레드(`gui._job_worker`·`_preview_worker`·`_estimate_worker`·`update_check`)는 Tk 객체·App·콜백을 쥐지 않는다. 평범한 값과 `queue`만 받고, Tk 쪽이 `after`로 가져간다(`test_no_worker_thread_holds_the_window`).
- 창을 닫을 때 `var_pages`의 trace를 푼다. Tcl 명령 표에 남은 콜백이 App을 영원히 붙잡는다(`test_window_is_released_after_closing_during_a_request`).
- `ImageTk.PhotoImage(..., master=위젯)`: 한 프로세스에 Tk가 여럿이면 기본 루트가 죽은 것일 수 있다.
- 화면 문자열은 전부 `lang/*.json`(4개 언어, 같은 키·치환자). `python tests/check_i18n.py .`가 0이어야 한다. 코드의 비ASCII 문자열은 언어 이름뿐(docstring·주석도 영어로).
- 설정 값은 타입을 검사하고 어긋나면 기본값(`gui._saved_state`, `update_check.prefs_from_settings`).
- 용량 줄이기는 이미지 스트림만 바꾼다(`page.replace_image`). 1비트·SMask/Mask 이미지는 건너뛰고, 새 스트림이 95% 미만일 때만 교체한다. 결과가 원본보다 작지 않으면 `NotSmaller`로 결과를 지운다.
- 버전은 `version.py` 한 곳. 릴리스마다 올린다(창 제목·`--version`·User-Agent·릴리스 노트).

## 검증 순서
1. `python -m pytest tests -q` (GUI 시험 포함, 약 4분. `timeout`을 걸고 돌린다.) 릴리스 전에는 3회 연속 + `PMS_SEEDS=30`(무작위 시나리오 `tests/test_scenarios.py`, 실패한 시드는 `-k seed7`로 재현). 엣지 케이스는 `tests/test_edge_cases.py`(색 공간·Decode·중첩 이미지·배치 dpi·메모리·취소 시점·폼/주석/회전 합치기·입력=출력 금지).
2. GUI를 바꿨으면 `python tests/shots.py <폴더>`로 4개 언어를 찍고 **이미지를 직접 연다**. 검게 나오면 화면 잠금부터 확인.
3. 빌드는 PowerShell에서 `build.bat` 절대 경로로. 빌드 전 `Get-Process pdf-merge-split`로 켜 둔 앱이 있는지 확인. 빌드 뒤 `python tests/exe_check.py dist\pdf-merge-split.exe`(오프라인) → 릴리스 직전 `--online` 1회.
4. 커밋 전 스테이징된 파일에서 개인 경로(`C:\Users\...`)를 검색한다.
