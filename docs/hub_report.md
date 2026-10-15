# 허브(chaekgalpi) 툴 페이지 등록용 자료 — pdfmerge v0.1.0 (2026-10-15)

툴 슬러그 `pdfmerge` · 저장소 `microhan1/pdf-merge-split` · exe `pdf-merge-split.exe` · 라이선스 AGPL-3.0

## 한 줄 소개
- ko: PDF 합치기·병합·압축·용량 줄이기를 파일을 올리지 않고 내 PC에서. 설치 없는 exe 하나, 쪽수 제한 없음, 원본 무수정.
- en: Merge and compress PDFs on your own PC - nothing is uploaded. One exe, no install, no page limit, originals untouched.

## 검색어(네이버 10-15 실측) → 제목·본문에 넣을 표기
pdf 합치기(50,510) · pdf 용량 줄이기(26,130) · pdf 병합(17,430) · pdf 압축(6,550). 네 표기를 제목·첫 문단·FAQ에 모두 쓴다.

## 대표 이미지
- `docs/hero_1200.jpg` (1200px 폭 JPG, 한국어 화면 — 합치기 탭, 파일 3개와 미리보기). 숫자가 보이는 쪽이 낫다면 `docs/done_ko.png`(「2.0 MB → 743 KB (37%)」가 기록에 찍힌 화면)를 1200px로 줄여 써도 됨
- 그 밖: `docs/gui_ko.png` `docs/gui_en.png` `docs/gui_zh-CN.png` `docs/gui_ja.png` `docs/shrink_ko.png` `docs/done_ko.png` `docs/update_notice.png`

## FAQ 후보

### 한국어
1. **파일이 서버로 올라가나요?** — 아니요. 합치기와 용량 줄이기 모두 내 PC 안에서만 처리하고, 인터넷 연결 없이도 동작합니다. 유일한 예외는 새 버전 확인으로, 도구 이름과 버전만 보내며 설정에서 끌 수 있습니다.
2. **용량을 줄이면 글자 검색이나 책갈피가 사라지나요?** — 아니요. 이미지만 다시 압축하고 글자층·책갈피(목차)·링크·쪽 크기는 그대로 둡니다. 글자로만 된 PDF는 거의 줄지 않습니다.
3. **얼마나 줄어드나요?** — 스캔 PDF(300 dpi)는 「보통·200 dpi」에서 보통 1/3~1/2, 「최소·150 dpi」에서 1/5 안팎까지 줄어듭니다. 실행 전에 예상 크기를 보여 줍니다. 결과가 원본보다 작지 않으면 파일을 만들지 않고 알립니다.
4. **합칠 때 순서와 책갈피는요?** — 목록에서 끌어 순서를 바꾸고, 파일마다 넣을 쪽(예: 1-10, 15)을 정할 수 있습니다. 각 파일의 책갈피는 파일 이름 아래에 묶여 그대로 들어갑니다.
5. **암호가 걸린 PDF도 되나요?** — 안내한 뒤 건너뜁니다. 암호를 풀어 주지는 않습니다.
6. **나누기는 없나요?** — 쪽 범위·N쪽마다·북마크 단위 나누기는 v0.2에 들어갑니다.

### English
1. **Does it upload my files?** — No. Merging and compressing happen entirely on your PC and work offline. The only exception is the update check, which sends just the tool name and version and can be turned off.
2. **Does compressing lose the text, bookmarks or links?** — No. Only the images are re-encoded; the text layer (search), bookmarks, links and page sizes stay as they are. Text-only PDFs barely shrink.
3. **How much smaller does it get?** — A 300 dpi scan typically drops to a third or a half at "normal, 200 dpi" and to about a fifth at "minimum, 150 dpi". An estimate is shown before you run; if the result would not be smaller, nothing is written and you are told.
4. **What about the order and the bookmarks when merging?** — Drag the files into order and give each one a page range (e.g. 1-10, 15). Every file's bookmarks are kept under an entry named after the file.
5. **Password-protected PDFs?** — They are skipped with a message; passwords are not removed.
6. **Can it split a PDF?** — Splitting (page ranges, every N pages, by bookmark) is planned for v0.2.
