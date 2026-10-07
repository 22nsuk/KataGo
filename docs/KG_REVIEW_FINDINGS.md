# KG-next fork review

Review date: 2026-10-07. Tree reviewed: `0feaf24d35f75d6b424b7db3f24063d5a78fa1be` on `master`, compared with `lightvector/KataGo` `master` at `d91ea855110dae533f0aada947b2b7d78cc8a4e1`.

The fork has issues disabled. This note is the findings record. It does not open GitHub issues.

This pass is a static review of the fork delta, docs, workflows, and the green CI history on that commit. It did not rebuild KataGo, rerun the GTP probe, or execute a GPU. Hosted CI success is not GPU inference acceptance.

## Release-team summary

Ship the current fork as a KG-next engine only with the limits below. There is no proven blocker in the master tree from this review. The reuse extension, Windows CUDA bundle checks, and the backported training-data / Python Board fixes are covered by tests and by green hosted runs on `0feaf24d`. Two contract/doc gaps are corrected in the same change as this note: the JSON analysis engine does not honor `reuseRootTree`, and the CI guide now includes the Python board workflow.

Before the next catalog promotion:

1. Treat root-reuse and Windows CUDA as release gates. They do not run on `master` push. The `kg-next-0feaf24d35f7` tag was covered only because those workflows were dispatched by hand and succeeded.
2. Leave [PR #5](https://github.com/22nsuk/KataGo/pull/5) as an upstream-review mirror. Do not retarget or merge it into this fork's `master`. The terminal-leaf test fix is already on `master`.
3. Keep `hardwareAcceptanceStatus: PENDING_HARDWARE` on the bundle receipts. The `kg-next-0feaf24d35f7` release notes add an operator RTX 4090 smoke for White, four threads, one test model, and root visit cap 32. This review did not see those logs in CI artifacts and does not treat that note as full GPU acceptance. The written bar is both colors and one/four threads (`docs/KG_WINDOWS_CUDA_BUNDLES.md`).
4. Send upstream candidates from the isolated branches, not from fork `master`. Upstream PR creation previously returned HTTP 403.

## Delta versus upstream

`git merge-base` is current upstream `master`. The fork is **34 commits ahead and 0 behind**. `git diff --stat upstream/master...HEAD` is 39 files, about +4090 / −138. No upstream commit is missing.

| Area | Why it exists for KG-next | Main files |
| --- | --- | --- |
| Root-only search reuse | Opt-in GTP `reuseRootTree` / `kg-reuse-root-tree` → `root-only-v1`, so allow/avoid changes can keep a first-ply tree | `cpp/search/search.cpp`, `searchexplorehelpers.cpp`, `searchhelpers.cpp`, `searchresults.cpp`, `searchupdatehelpers.cpp`, `cpp/command/gtp.cpp`, `docs/GTP_Extensions.md`, `docs/KG_ROOT_REUSE.md`, `python/probe_kg_root_reuse.py` |
| Windows CUDA 12.8 and 13.2 source bundles | Hash-locked SDK, PE audit, slim ZIP named from the source SHA, receipts stuck at `PENDING_HARDWARE` | `scripts/windows_cuda_sdk.py`, `scripts/build_windows_cuda_bundle.py`, `scripts/windows_cuda12.lock.json`, `scripts/windows_cuda13.lock.json`, `.github/workflows/windows-cuda-bundles.yml`, `cpp/CMakeLists.txt` (honor an explicit architecture list) |
| CI cost controls | Docs-only skip of the six platform jobs, ccache / vcpkg binary cache, CUDA download cache that still re-hashes inputs | `scripts/ci_scope.py`, `.github/actions/ccache/action.yml`, `.github/workflows/build.yml`, `docs/KG_CI.md` |
| Training-data corrections | Scored-SGF near-end thresholds, Fox handicap-komi fallthrough, per-turn empty Q slots | `cpp/command/writetrainingdata.cpp`, `python/test_fox_sgf_filter.py` |
| Python Board hash | Backport of upstream PR #1256, plus extra capture/undo cases and a small workflow | `python/katago/game/board.py`, `python/tests/test_board_zobrist.py`, `.github/workflows/python-board.yml` |
| Visit-cap test invariant | Terminal leaves have no snapshot to freeze | `cpp/tests/testsearchnonn.cpp` (also the body of PR #5) |

Default `setAvoidMoveUntilByLoc` still clears the tree. Reuse runs only when GTP passes `reuseRootTree true` and both the old and new masks are empty or depths 0–1 (`cpp/search/search.cpp` around the `rootOnly` check). Deeper masks, `clear_cache`, komi, rules, and player changes still clear.

## What held up

- **Reuse contract vs code.** Masked root children stay allocated and are dropped from selection, analysis rows, aggregate weight, ownership, and dame-pass suppression. A current-player mask change deletes the root visit-cap snapshot and restarts the counted interval from `rootVisitCapStartVisits`. Opponent-only and empty/all-zero masks leave that interval alone. Symmetry pruning turns off for the rest of the position when the duplicate-location partition changes, and `beginSearch` then stops deleting those children (`cpp/search/search.cpp` symmetry block; `isAllowedRootMove` in `cpp/search/searchhelpers.cpp`). Analysis weight factors use the compacted reported-child index after masked rows are skipped (`cpp/search/searchresults.cpp`, `statsBuf[buf.size()-1]`).
- **Probe fail-closed behavior.** Import rejects `-O` / `PYTHONOPTIMIZE`. The output directory must be new. `result.json` is written only after the checks, a zero exit, reader joins, and log flushes (`python/probe_kg_root_reuse.py`). Lifecycle failures have a separate unittest.
- **CUDA supply chain on the fork-owned path.** Lock entries require HTTPS and a 64-hex SHA-256. Downloads use `curl --proto =https`. Cache hits call `verify_input` again (`scripts/windows_cuda_sdk.py`). Zip members go through `safe_name`. The executable is rejected if it imports `z.dll`, `zip.dll`, or the dynamic MSVC runtime. `version` is started with `PATH` limited to System32. Receipts set `PENDING_HARDWARE` in code (`scripts/build_windows_cuda_bundle.py`). Workflow permissions are `contents: read`. There is no `pull_request_target`.
- **Architecture lists match.** CMake's CUDA 12.8 default (`cpp/CMakeLists.txt`, the `VERSION_GREATER_EQUAL 12.8` branch) matches `scripts/windows_cuda12.lock.json` `gpuArchitectures`. The CUDA 13 default matches `scripts/windows_cuda13.lock.json`. The bundle also passes `CMAKE_CUDA_ARCHITECTURES` explicitly, and CMake now keeps that list.
- **Latest hosted evidence on `0feaf24d` (2026-10-06), all success:** Build and Test run `37419628825`, Python board run `37419628780`, Windows CUDA dispatch run `37419633907`, root-reuse dispatch run `37419636655`. Those CUDA and root-reuse runs are `workflow_dispatch`, not `push`.

## Ranked follow-ups

Severity here means release risk if left as-is. Effort is the kind of change, not a schedule.

### 1. Should-fix — release gate for the two fork workflows

**Fork-owned. Not fixed in this change** (the CI doc already chooses manual dispatch to avoid a CUDA build on every `master` push).

`.github/workflows/kg-root-reuse.yml` and `.github/workflows/windows-cuda-bundles.yml` trigger on path-filtered `pull_request` and `workflow_dispatch` only. A merge commit is not rebuilt by them. If `master` moved after the PR run, the merge tree can differ from the tested head. Build and Test on `push` does not run the GTP probe, the Fox SGF converter, or the CUDA bundle.

Evidence: workflow `on:` blocks; `docs/KG_CI.md` manual-dispatch paragraph; green dispatches `37419636655` and `37419633907` for tag `kg-next-0feaf24d35f7`.

Release step: dispatch both workflows on the exact tag SHA and require success before catalog promotion. Turning them into required `push` checks is a cost decision the CI guide currently declines.

### 2. Should-fix — do not merge PR #5 into fork master

**Fork process. The patch is an upstream candidate and is already on master.**

[PR #5](https://github.com/22nsuk/KataGo/pull/5) targets `codex/upstream-review-base-20261005`, not `master`. Its two commits (`aeaf01d1`, `0750698f`) have the same patch-ids as `54268eb6` and `f8ce87e8` on master. `git merge-tree` of that branch into master reports `changed in both` for `cpp/tests/testsearchnonn.cpp`. Merging or retargeting it would conflict with later reuse tests and would not add the terminal-leaf fix again.

Upstream submission of this branch and `codex/upstream-near-end-exact` returned HTTP 403 (`Resource not accessible by integration`), recorded in the PR body. Resubmit those isolated branches when credentials allow. Do not open an upstream PR from fork `master`.

### 3. Should-fix — JSON analysis engine is outside the reuse contract

**Fork-owned. Documented in this change.**

`cpp/command/analysis.cpp` calls `bot->setAvoidMoveUntilByLoc(...)` with the default `reuseRootTree = false` (the call near the analysis loop). GTP parses the flag only for `kata-analyze`, `kata-genmove_analyze`, `kata-search_analyze`, and `kata-search_analyze_cancellable` (`cpp/command/gtp.cpp`, `isKata` and the `reuseRootTree` key). `docs/KG_ROOT_REUSE.md` now says so. Wiring reuse into the JSON engine would be a new feature, not a doc fix.

### 4. Should-fix — CI guide omitted the Python board workflow

**Fork-owned. Corrected in this change.**

`docs/KG_CI.md` listed four workflows and said all four share the PR cancellation rule. `.github/workflows/python-board.yml` is a fifth workflow with the same concurrency shape, path filters, and 7-day JUnit retention. The guide's table and the "five workflows" sentence now include it. `scripts/test_ci_policy.py` now checks that workflow's concurrency group too.

### 5. Should-fix — next upstream sync of the Python Board fix

**Upstream-candidate already on the fork. Keep the fork tests when rebasing.**

`python/katago/game/board.py` XORs `ZOBRIST_STONE[pla]` for the stones being removed. That one-line change matches open upstream PR [lightvector/KataGo#1256](https://github.com/lightvector/KataGo/pull/1256). The fork test file adds chain-capture, both-color, and pass cases that the upstream PR's 60-line test file does not have (`python/tests/test_board_zobrist.py`). Expect a conflict on that test file. Keep the fork cases. The production line should merge cleanly.

### 6. Upstream candidates already fixed here, still absent from upstream master

Send these as their own upstream PRs. They are not reasons to PR the reuse or CUDA work.

| Item | Evidence on the fork | Upstream home |
| --- | --- | --- |
| Visit-cap terminal leaves | `cpp/tests/testsearchnonn.cpp`; PR #5 body; patch-id match above | Isolated branch `codex/upstream-terminal-visit-cap` |
| Scored-SGF near-end ratios | `isNearEndByOwnership` in `cpp/command/writetrainingdata.cpp`; `Tests::runTrainingDataEndTests` from `cpp/command/runtests.cpp` | Branch `codex/upstream-near-end-exact` (`90126b1`) |
| Fox handicap-1 / komi mismatch fallthrough | `reportSgfDone(..., "GameHandicap1MismatchKomi"); return;` in `writetrainingdata.cpp` | No isolated branch found. Needs its own upstream patch. `python/test_fox_sgf_filter.py` is the fork regression |
| Empty per-turn Q slots for human SGFs | `whiteQValueTargets(whiteValueTargets.size())` before `addRow` in `writetrainingdata.cpp`; NPZ check in `python/test_fox_sgf_filter.py` (`qValueTargetsNCMove` all zeros) | Same Fox patch is a reasonable bundle |

`trainingwrite.cpp` asserts `whiteValueTargetsIdx < whiteQValueTargets.size()`. The old empty vector failed that assert on SGF rows. The fork sizes the vector and still writes no searched Q values.

### 7. Nice — pin the remaining floating Actions, and hash TheRock only if you touch it

**Mostly inherited from upstream. Fork-owned workflows are already pinned.**

`Build and Test` and `ONNX backend` still use `actions/upload-artifact@v4` (and ONNX helpers use `actions/checkout@v4` / `actions/cache@v4`). New fork workflows pin `actions/upload-artifact` to `ea165f8d65b6e75b540449e92b4886f43607fa02` and `actions/cache` to `0057852bfaa89a56745cba8c7296529d2fc39830`. The Linux OpenCL upload the fork rewired (`build.yml` "Upload artifact") is still `@v4`.

The Windows ROCm job still downloads the TheRock tarball by URL and version with no SHA-256. `docs/KG_CI.md` already says this change did not add a hash guarantee for TheRock or ONNX provider caches. Leave it unless that job is edited.

### 8. Nice — lockfile runtime names are not passed through `safe_name`

**Fork-owned hardening. Current lockfiles are plain DLL names.**

`scripts/windows_cuda_sdk.py` `safe_name` rejects traversal in CUDA zip members. `runtimeFiles` entries are copied as `prefix / "runtime" / name` and are only checked for duplicates in `load_lock`. A future lock edit could put `..` in a runtime name. Rejecting any name that is not a single filename would close that. The checked-in 12.8 and 13.2 lists do not do this today.

## Upstream-merge risk

Rebasing onto today's upstream `master` is a no-op: the fork already contains it. The risky future conflicts are the search files above, `cpp/command/gtp.cpp`, `cpp/command/writetrainingdata.cpp`, `cpp/CMakeLists.txt`, and `.github/workflows/build.yml` / `onnx-backend.yml`.

Keep reuse and the CUDA bundle recipe on this fork. Upstream KataGo does not advertise `kg-reuse-root-tree`. A stock upstream binary cannot satisfy the KG-next capability query (`docs/KG_CUDA13_WINDOWS.md`).

CMake's user-architecture guard exists so a KG-next single-GPU or lockfile architecture list is not replaced by the version default (`cpp/CMakeLists.txt`, `_katago_cuda_architectures_given` before `enable_language(CUDA)`). That guard is fork-specific and should stay with the bundle recipe.

## Limits

- Hosted runners have no NVIDIA GPU. `runtests`, `runoutputtests`, PE import audits, and `version` do not prove GPU inference.
- Bundle receipts hard-code `hardwareAcceptanceStatus: PENDING_HARDWARE` and a reason string in `scripts/build_windows_cuda_bundle.py`. Publishing a release or updating the KG-next catalog is a separate step (`README.md`, `docs/KG_WINDOWS_CUDA_BUNDLES.md`).
- Release `kg-next-0feaf24d35f7` repeats `PENDING_HARDWARE` for the original receipts and then describes a local RTX 4090 / driver 610.88 smoke: both exact executables, 39 root-reuse cases, model `g170-b6c96`, White, four threads, ownership, root visit cap 32. The release text calls that scoped smoke, not full acceptance. This review did not reproduce it.
- The manual CUDA 13 recipe (`docs/KG_CUDA13_WINDOWS.md`, installed toolkit, CMake 4.1.3 or newer, one architecture) is a different recipe from the hash-locked CI bundles (CMake 3.31.6, Ninja, both lockfile architecture lists).
- CPU Eigen probe success does not transfer to the Windows CUDA ZIP. The CUDA workflow does not run `python/probe_kg_root_reuse.py`, because that probe initializes the network.
- Slim ZIPs omit NVIDIA runtime DLLs and models on purpose.
- Fox SGF tests use synthetic 30-move records (`python/test_fox_sgf_filter.py`). They do not certify every real Fox handicap file.
