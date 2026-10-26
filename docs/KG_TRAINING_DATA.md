# SGF 학습 데이터 변환

`katago writetrainingdata`는 SGF 파일을 병렬 처리하며, worker마다 `Search`와 학습 데이터
출력 버퍼를 갖습니다. 변환에 사용하는 config에 다음 설정을 추가할 수 있습니다.

```ini
numWorkerThreads = 4
```

기존 명령의 `-override-config "numWorkerThreads=4"`로도 지정할 수 있습니다.
허용 범위는 1–1024이며, 생략하면 기존 대량 변환의 상한인 64를 유지합니다.
범위 밖 값이나 정수가 아닌 값은 NN 초기화 전에 오류 코드 1로 종료합니다.
유효 worker 수는 다음 세 값의 최솟값입니다.

- 설정한 `numWorkerThreads`(기본 64)
- 수집된 SGF 파일 수
- `-max-files-to-load`로 제한한 파일 수

예를 들어 4 workers를 요청해도 파일이 2개면 2개만 만들고, 파일 제한이 1이면 1개만
만듭니다. 이 수는 NN 초기화의 예상 동시 평가 수, worker별 `Search`·출력 버퍼,
SGF 작업 실행, 남은 버퍼 저장과 정리에 함께 적용됩니다. 각 worker의 검색 스레드는
기존처럼 1개입니다. 시작 로그에는 처리 대상 수와 유효·설정 worker 수가 기록됩니다.

파일 수는 SGF 내용의 적합성·제외 조건을 검사하기 전의 수입니다. 입력이 없거나
`-max-files-to-load 0`이면 NN 모델과 worker 자원을 초기화하지 않고 정상 종료하며,
로그의 총 행 수는 0이고 NPZ는 만들지 않습니다. 따라서 이 경우에는 모델을 열지 않습니다.
기존처럼 NPZ가 이미 들어 있는 출력 디렉터리는 사용하지 않습니다.

`nnMaxBatchSize`는 worker 수를 조절하지 않습니다. NN 백엔드의 배치 정책과 명시적인
NN 서버 스레드 설정은 그대로 적용되며, worker 설정이 이 값들을 강제로 덮어쓰지는 않습니다.

## 메모리 용량과 성능 해석

19×19 최대 보드로 컴파일하고 `dataBoardLen=19`, `maxApproxRowsPerTrainFile=10000`을
사용하면, 각 출력 버퍼는 최대 14,155행을 위한 배열을 요청합니다. 현재 배열 형식과
헤더·임시 unpack 배열을 합한 요청 용량은 worker당 약 114.0MiB, 64개면 약 7.125GiB입니다.
4개면 같은 조건에서 약 456.0MiB입니다. 이 계산에는 `Search`, 모델, NN 캐시, 압축 및
기타 메모리가 포함되지 않으며, 실측 RSS를 뜻하지 않습니다.

worker 수는 자원 사용량을 조절하는 설정입니다. 처리량 향상은 보장하지 않으며,
하드웨어·모델·입력 기보를 고정한 별도 측정이 필요합니다. 병렬 처리와 난수 사용 때문에
worker 수를 바꾸면 출력 파일명, 파일 분할, 행 순서 및 난수 표본이 달라질 수 있습니다.
기존 SGF 필터와 파일 제한의 의미는 유지합니다.

## 회귀검사

시간 설정의 숫자·형식·범위 파싱 오류는 해당 기보를 `BadTimeControl`로 집계하고
건너뜁니다. 날짜 파싱 오류는 `GameBadDate`로 집계합니다. GoGoD의 날짜 보정과
연도를 찾을 수 없을 때의 기존 `GameDateParsing` 분류는 유지합니다. 로그에는 오류가
발생한 파일과 이유가 기록되고, 나머지 기보는 계속 변환되며 이미 버퍼에 쌓인 정상 행도
저장됩니다. 이 처리는 시간·날짜 파싱 오류에 한정하며 모델 로딩·검색·출력 저장 오류를
정상적인 기보 제외로 바꾸지 않습니다.

```sh
python3 python/test_training_data_metadata.py --engine build-eigen/katago \
  --model cpp/tests/models/g170-b6c96-s175395328-d26788732.bin.gz \
  --output training-metadata-proof
```

Fox·OGS·KGS·Go4Go·GoGoD에서 정상 기보 → 오류 기보 → 정상 기보 순서를 고정해
검사합니다. 오류 전후의 흑승·백승 기보가 실제 NPZ에 각각 한 번씩 보존되는지, 제외
사유와 처리 건수가 맞는지 검증합니다. 정상 시간 형식과 GoGoD 날짜 보정, 오류 기보만
있는 입력, 복수 worker의 혼합 입력 및 모델 로딩 실패도 검사합니다.

```sh
python3 python/test_training_data_workers.py --engine build-eigen/katago \
  --model cpp/tests/models/g170-b6c96-s175395328-d26788732.bin.gz \
  --output training-workers-proof
```

Eigen CPU 빌드로 실제 변환을 실행합니다. 기본값, 1·2 workers, 파일 수보다 큰 설정,
설정 상한, 파일 제한, 빈 입력, 제한 0 및 잘못된 설정을 검사합니다. NN 서버 스레드 로그와
실제로 생성된 worker 수, 처리 건수, NPZ 행 수 및 임시 파일 정리를 확인합니다.
입력이 없는 두 사례는 존재하지 않는 모델 경로로 실행해 NN 로딩을 생략하는지 검증합니다.

승패 목표 회귀검사는 다음 명령으로 실행합니다.

```sh
python3 python/test_training_data_results.py --engine build-eigen/katago \
  --model cpp/tests/models/g170-b6c96-s175395328-d26788732.bin.gz \
  --output training-results-proof
```

`RE[B+]`와 `RE[W+]`는 점수를 생략한 흑승·백승입니다. Fox·OGS 기보, 소문자·공백 정규화,
기존 수순 누락 표기를 포함한 8개 사례에서 실제 NPZ의 240개 행을 읽고, 각 수의 착수자
관점에서 승패 목표와 학습 가중치를 검증합니다. 기존 Fox 검사의 행 수·빈 Q 목표 검사도
함께 적용합니다.

이전 버전은 `RE[W+]`를 흑승으로 처리했습니다. 해당 기보를 포함해 생성한 학습 데이터는
수정된 엔진으로 다시 변환해야 합니다. 이미 저장된 NPZ를 자동으로 수정하지는 않습니다.
