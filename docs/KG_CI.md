# 포크 CI와 캐시 운영

[포크 시작 안내](../README.md) · [루트 탐색 재사용](KG_ROOT_REUSE.md) · [Windows CUDA 번들](KG_WINDOWS_CUDA_BUNDLES.md) · [검토 메모](KG_REVIEW_FINDINGS.md)

## 실행 범위와 비용 관리

캐시는 재사용할 입력이나 컴파일 결과를 제공할 뿐, 검증 성공을 대신하지 않습니다.
코드가 바뀌면 기존 플랫폼 빌드와 네이티브 테스트를 유지합니다.

| 워크플로 | 자동 실행 | 수동 실행과 검증 범위 |
| --- | --- | --- |
| CI | 모든 `master` 대상 PR와 `master` push | 변경 범위를 계산하고 아래 재사용 워크플로를 호출한 뒤, 항상 `ci-required`로 결과를 집계합니다. 수동 실행은 모든 기본 검사를 선택합니다. |
| Build and Test | CI가 항상 호출합니다. 문서만 바뀐 것이 확인될 때에만 6개 플랫폼 빌드를 건너뜁니다. | 가벼운 정책 검사와 OpenCL·Metal·ROCm 빌드 및 `runtests`를 유지합니다. GPU 실기 승인이 아닙니다. |
| Root tree reuse | 문서 전용을 제외한 PR와 `master` push에서 CI가 호출합니다. | Eigen 빌드, 네이티브 테스트, 루트 재사용 GTP 6조합, 분석 출력 필터 72조합, 파라미터 명령 탐지·JSON 설정, Fox 변환, 변환 worker 제한, SGF 승패 학습 목표 및 메타데이터 오류 격리 검증을 모두 실행합니다. |
| ONNX backend build & test | C++·CI 설정 변경의 PR와 `master` push에서 CI가 호출합니다. | 기본은 CPU·DirectML 3조합입니다. 개별 수동 실행의 `include_slow=true`에서만 OpenVINO·TensorRT 소스 빌드를 추가합니다. |
| Windows CUDA source bundles | C++·번들 스크립트·잠금 파일·CI 설정 변경 PR에서 CI가 호출합니다. | CUDA 12·13 프로필을 모두 빌드·검사합니다. `master` push는 중복 번들을 만들지 않으며, 릴리스 소스 검증은 별도로 수동 실행합니다. |
| Python board regressions | 바둑판 코드·테스트·상위 conftest·pytest 설정 또는 CI 설정 변경의 PR와 `master` push에서 CI가 호출합니다. | Python 3.12에서 Board 해시·캡처 undo 회귀를 실행합니다. |

문서 전용 판정은 루트의 Markdown, `docs/`의 Markdown, `LICENSE`, `CONTRIBUTORS`로
한정합니다. 코드 삭제·코드에서 문서로의 이름 변경·알 수 없는 파일은 빌드를 유지합니다.
PR은 merge-base부터 head까지, push는 before/after 전체 차이를 읽습니다. 300개 파일
상한에 의존하지 않으며, 변경 집합이 비어 있거나 이력·이벤트를 읽을 수 없어도 빌드합니다.
자동 실행 이벤트는 `.github/workflows/ci.yml` 한 곳에서 소유합니다. 개별 워크플로는
`workflow_call`과 `workflow_dispatch`만 유지하므로 같은 PR에서 자동 빌드를 두 번 실행하지 않습니다.
`scripts/ci_scope.py`가 실제 Git 변경 목록으로 선택하며, CI 설정 변경이나 불완전한 이력은
검사를 보수적으로 유지합니다. CUDA의 PR/수동 전용 정책은 유지합니다.

호출자와 다섯 재사용 워크플로는 서로 다른 concurrency 그룹을 사용하고,
**같은 PR의 오래된 실행만 취소**합니다. 다른 PR, `master` push,
수동 실행은 실행 ID로 분리해 서로 취소하지 않습니다. 기존 업로드 종류를 유지하면서
일반 플랫폼·ONNX 산출물의 보관은 14일로 제한합니다. root 증거와 Python board JUnit
결과는 7일, CUDA 산출물·증거는 14일입니다. 중요한 증거는 만료 전에 별도로 보존해야 합니다.

## 필수 체크와 병합 보호

`ci-required`는 `always()`로 실행되어 scope 선택, 일반 빌드/정책 검사, 선택된
엔진·ONNX·CUDA·Board 검사의 결과를 확인합니다. 선택된 검사의 실패·취소·생략,
누락된 결과, 잘못된 scope 출력은 모두 실패입니다. `false`로 명시적으로 제외한
검사만 `skipped`를 허용합니다. 이 판정은 Python 최적화 모드에서도 비활성화되지 않습니다.

문서 전용 PR에서도 CI와 정책 검사 및 최종 체크가 실행됩니다. 따라서 경로 필터로
워크플로 전체가 생략되어 필수 체크가 영구 대기하는 문제가 없습니다. `scripts/test_ci_gate.py`는
실제 `needs` 형태의 입력으로 성공·실패·취소·정상 생략·누락과 CLI 종료 코드를 검사합니다.

저장소의 `master` 보호 설정에서는 GitHub Actions가 보고하는 `ci-required`를 필수로 지정하고,
최신 base 반영, PR을 통한 병합, 관리자에게도 규칙 적용, force push 및 브랜치 삭제 금지를
사용합니다. 개인 포크에서 본인 PR을 승인할 수 없는 상황을 피하도록 승인 리뷰 수는 0으로
두며, CI 성공과 PR 절차는 계속 요구합니다. 설정은 YAML과 별개의 GitHub 저장소 상태이므로
워크플로 파일만 추가해도 보호가 자동으로 생성되는 것은 아닙니다.

기존 PR에 새 CI 워크플로가 없는 경우에는 이 CI 변경을 먼저 병합하고 기존 PR에 최신
`master`를 반영해야 `ci-required`가 생성됩니다. 옛 커밋의 수동 실행 성공을 새 커밋의
필수 체크 대신 사용하지 않습니다.

## 릴리스 소스와 검증 대상

PR 실행에서 Root tree reuse는 checkout 기본값인 **시험 병합 커밋**을 검사합니다.
Windows CUDA source bundles는 `SOURCE_SHA`로 지정한 **PR head**를 빌드합니다.
Root 검사는 코드 변경이 있는 `master` push에서도 실행됩니다. CUDA 번들은 PR/수동
실행만 유지하므로, PR의 성공만으로 이후 릴리스 커밋과 배포 ZIP까지 검증됐다고 보지 않습니다.

카탈로그 승격 전에는 릴리스 소스 SHA를 고정하고, 그 커밋을 가리키는 태그 또는 브랜치
이름을 `workflow_dispatch`의 `ref`로 사용해 두 워크플로를 실행합니다. 두 실행의
`head_sha`, checkout 기록과 CUDA receipt의 `sourceCommit`이 의도한 릴리스 SHA와
일치하는지 확인하고, root 검사와 CUDA 12·13 두 프로필이 모두 성공해야 합니다.
실행 링크·probe 결과·배포 파일 해시를 함께 보존합니다. 이 릴리스 산출물 검증은
`ci-required`를 통한 소스 병합 검증과 별개이며, 병합 체크 성공으로 대신하지 않습니다.

이 성공은 GPU 추론 승인이 아닙니다. 배포할 정확한 실행 파일과 모델·설정으로
[Windows CUDA 실기 승인 범위](KG_WINDOWS_CUDA_BUNDLES.md)를 별도 검증하고,
원본 receipt의 `PENDING_HARDWARE`는 유지한 채 실기 증거를 따로 기록합니다.
한 모델·한 색·한 스레드 설정의 smoke 결과를 전체 실기 승인으로 확대하지 않습니다.

## 캐시 경계

| 캐시 | 저장 대상과 키 | 캐시가 맞아도 수행하는 작업 |
| --- | --- | --- |
| C++ 컴파일 | Linux OpenCL·macOS OpenCL/Metal·Eigen에서 `ccache`만 저장합니다. OS·아키텍처·백엔드·실제 compiler/CMake/ccache 버전 및 runner 이미지·공용 action 내용으로 구분하고 소스 SHA별로 저장합니다. 같은 namespace 안에서만 이전 캐시를 복원합니다. | CMake configure, 빌드·링크, 모든 테스트. ccache가 소스·옵션·헤더를 확인하며 compiler 내용 비교를 사용합니다. |
| Windows OpenCL 의존성 | vcpkg의 ABI별 binary archive만 저장합니다. OS·아키텍처·runner 이미지·vcpkg revision·triplet·워크플로 내용의 정확한 키를 사용합니다. | `vcpkg install`을 항상 실행해 현재 ABI에 맞는 패키지만 사용합니다. |
| Windows CUDA 다운로드 | 대상 프로필·OS·아키텍처·해당 `windows_cuda*.lock.json` 해시로 구분합니다. 압축 다운로드만 저장하며 다른 잠금 파일로의 fallback은 없습니다. | 모든 입력의 잠금 SHA-256/기재된 크기를 재검사한 뒤 새 SDK 구성, 소스 빌드, PE 검사, 네이티브 테스트, ZIP 검증을 수행합니다. |
| 기존 TheRock·ONNX 의존성 | 기존 공급자별 캐시와 검증 방식을 유지합니다. | 이 PR이 기존 공급자 캐시에 새로운 서명·해시 보증을 추가하는 것은 아닙니다. |

CMakeCache, CMakeFiles, Ninja 상태 파일은 더 이상 공유 캐시로 복원하지 않습니다.
SDK 경로·Homebrew 경로·파일 시간 정보가 과거 runner를 가리키는 문제를 피하고,
매번 현재 환경에서 configure합니다. 실행 파일, PASS 결과, CUDA SDK 설치본과
배포 ZIP도 새 캐시의 저장 대상이 아닙니다. ccache 크기는 job당 256 MB로 제한하며,
검사를 느슨하게 하는 sloppiness 설정은 추가하지 않습니다.

## 최초 실행, 재실행, 캐시 장애

첫 실행이나 runner·도구체인·잠금 파일 변경 뒤에는 cache miss가 정상입니다.
GitHub 캐시는 ref 범위로 격리됩니다. PR에서 만든 캐시는 다른 PR이 바로 읽지 못하며,
기본 브랜치의 캐시는 후속 PR에서 사용할 수 있습니다. 일반 플랫폼 빌드와 Root 검사는
`master` push가 캐시를 채웁니다. CUDA의 여러 PR 간 재사용이 필요하면 병합 후
해당 워크플로를 `master`에서 한 번 수동 실행할 수 있습니다. 캐시를 채우기 위한
주기적 추가 빌드나 유료 저장 한도 확대는 설정하지 않습니다.

캐시는 제거·만료·저장 실패할 수 있으므로 cold build가 계속 동작해야 합니다.
CUDA 캐시가 손상돼 검증에 실패하면 빌드를 승인하지 않습니다. Actions의 Caches에서
문제 키를 삭제한 뒤 다시 실행하거나, 캐시 namespace를 올려 새로 받습니다.
손상된 입력을 자동 승인하거나 잠금 해시를 우회하는 복구 경로는 없습니다.
용량이 큰 CUDA/TheRock 다운로드와 여러 소스 SHA의 ccache가 저장 공간을 경쟁할 수
있으므로 적중률·업로드 크기·퇴출을 확인한 뒤 캐시 유지 범위를 조정합니다.

## 검증과 효과 측정

```sh
python3 -m pip install PyYAML==6.0.2
python3 -B -m unittest discover -s scripts -p 'test_ci_*.py' -v
python3 -B -m unittest discover -s scripts -p test_windows_cuda_bundle.py -v
```

변경 범위 판정은 실제 임시 Git 저장소의 수정·삭제·이름 변경·310개 이상의 파일로
검사합니다. 캐시 입력 검사는 다운로드 함수 자체에 정상/손상된 캐시를 넣어,
네트워크를 사용하지 않는 cache hit에서도 무결성 검사가 유지되는지 확인합니다.
구조 검사는 키 격리·기존 테스트 단계·수동 ONNX opt-in이 의도대로 연결됐는지 확인하지만,
실제 플랫폼 CI 실행을 대신하지 않습니다.

분석 출력 필터 회귀는 실제 엔진으로 흑·백, `lz-analyze`·`kata-analyze`,
대칭 가지치기 켜짐·꺼짐에서 `minmoves`·`maxmoves`를 검사합니다. 최소 개수를 채우는
0방문 후보는 유지하고, 초과한 0방문 후보만 제거하며, 방문한 대칭 후보는 최소 개수를
넘더라도 유지하는지 확인합니다. 기존 `visitCapContempt = 2`로 루트의 선택 분포를 고정해
보고 시점에 따라 후보 수가 달라지는 것을 막고, 중단 응답 전까지 받은 보고서를 모두 검사합니다.

```sh
python3 python/test_gtp_analysis_filter.py --engine build-eigen/katago \
  --model cpp/tests/models/g170-b6c96-s175395328-d26788732.bin.gz \
  --output analysis-filter-proof
```

출력 디렉터리는 새 경로여야 하며, 실패해도 설정과 GTP·stderr 로그는 남습니다.
전체 검증과 정상 종료를 확인한 경우에만 엔진·모델 해시와 72개 사례를 담은
`result.json`을 기록합니다. 이 CPU 검증은 CUDA 빌드나 GPU 추론 검증을 대신하지 않습니다.

파라미터 명령 회귀는 `known_command`와 `list_commands`에서 단일·복수 파라미터
명령이 노출되는지, 미지원 명령은 `false`로 조회되는지, JSON으로 설정한 값을 복수·단일 조회로
다시 읽을 수 있는지 검사합니다. `kata-get-param-state`의 정확한 WRN/PDA 값과 설정 키
유무·문자열도 검증하며, PDA와 dynamic cap의 네 가지 설정 조합 및 indexed 키 사례를
흑·백 각각 실행해 총 12개 조합에서 복원 전후 실제 착수 검색의 PDA가 유지되는지 확인합니다.
WRN/PDA 하한·상한, 음의 0, 17자리 소수, 작은 지수 표기의 설정·변경·복원도 검사하며,
숫자는 `float.hex()`로 비교해 음의 0의 부호까지 보존하는지 확인합니다. 각 범위를 바로
벗어난 double 값 4개는 다른 유효 설정과 함께 요청해도 전체 갱신이 거부되어야 합니다.
명시적 0, GTP에서 무시되는 indexed 키, 잘못된 명령과 거부된 setter의 상태 보존도 검사합니다.
기존 probe의 프로세스 종료·로그 검증을 재사용하며,
CI의 `root-reuse-proof` 아티팩트에 `parameter-commands-proof` 결과를 함께 보관합니다.

```sh
python3 python/test_gtp_parameter_commands.py --engine build-eigen/katago \
  --model cpp/tests/models/g170-b6c96-s175395328-d26788732.bin.gz \
  --output parameter-commands-proof
```

속도 평가는 같은 소스와 같은 도구체인의 cold/warm 실행을 비교합니다. ccache 통계,
configure/build/test 시간, cache restore/save 시간과 크기를 분리해 읽어야 합니다.
히트가 있어도 링크·테스트·SDK 압축 해제는 계속 실행되므로 전체 시간이 같은 비율로
줄어드는 것은 아닙니다. 큐 대기시간이나 runner 차이를 제외하지 않은 두 실행만으로
보장된 절감률이나 금액을 주장하지 않습니다.

## 공식 참고

- [GitHub PR의 시험 병합 ref](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
- [GitHub 수동 실행의 branch/tag ref](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
- [GitHub 캐시 범위와 동작](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [GitHub 동시 실행과 조건부 취소](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency)
- [ccache 매뉴얼](https://ccache.dev/manual/latest.html)
- [vcpkg binary caching](https://learn.microsoft.com/en-us/vcpkg/users/binarycaching)
