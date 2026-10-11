# KG-next fork review

Review date: 2026-10-07; PR review clarification: 2026-10-08. Tree reviewed: [`0feaf24d35f75d6b424b7db3f24063d5a78fa1be`](https://github.com/22nsuk/KataGo/commit/0feaf24d35f75d6b424b7db3f24063d5a78fa1be) on `master`, compared with `lightvector/KataGo` at [`d91ea855110dae533f0aada947b2b7d78cc8a4e1`](https://github.com/lightvector/KataGo/commit/d91ea855110dae533f0aada947b2b7d78cc8a4e1). All delta counts and code observations below refer to these fixed commits, not a moving `master` or this documentation PR's head.

This note is the findings record. It does not open GitHub issues.

This pass is a static review of the fork delta, docs, workflows, and the hosted CI history on that commit. It did not rebuild KataGo, rerun the GTP probe, or execute a GPU. Hosted CI success is not GPU inference acceptance.

## Release-team summary

Retain the fork direction and merge the documentation/policy corrections, subject to the PR's checks. The static review did not establish a new engine-code blocker; that is not an approval to ship, a proof that the engine has no defects, or a performance/strength claim. The reuse extension, Windows CUDA bundle checks, and the backported training-data / Python Board fixes have the scoped test evidence linked below. The JSON/GTP boundary and Python board workflow documentation are corrected here; release acceptance remains separate.

Before the next catalog promotion:

1. Treat root-reuse and Windows CUDA as release gates. They do not run on `master` push. The source for `kg-next-0feaf24d35f7` has successful manual runs linked below; a different release source needs its own evidence.
2. Leave [PR #5](https://github.com/22nsuk/KataGo/pull/5) as an upstream-review mirror. Do not retarget or merge it into this fork's `master`. The terminal-leaf test fix is already on `master`.
3. Keep `hardwareAcceptanceStatus: PENDING_HARDWARE` on the bundle receipts. The [release notes](https://github.com/22nsuk/KataGo/releases/tag/kg-next-0feaf24d35f7) report an operator RTX 4090 smoke for White, four threads, one test model, and root visit cap 32. That release-text claim was not independently reproduced in this review and is not full GPU acceptance. The written bar includes both colors and one/four threads ([bundle acceptance procedure](KG_WINDOWS_CUDA_BUNDLES.md)).
4. Send upstream candidates from the isolated branches, not from fork `master`. PR #5 records earlier upstream submission attempts returning HTTP 403; this is historical submission evidence, not a claim about current permissions.

## Delta versus upstream

For the pinned comparison, the merge base is `d91ea855`. The fork is **34 commits ahead and 0 behind**, with 39 changed files, +4090 / -138 ([commit comparison](https://github.com/22nsuk/KataGo/compare/d91ea855110dae533f0aada947b2b7d78cc8a4e1...0feaf24d35f75d6b424b7db3f24063d5a78fa1be)). No commit from that upstream snapshot is missing. Recompute these figures when reviewing a later upstream or fork revision.

| Area | Why it exists for KG-next | Main files |
| --- | --- | --- |
| Root-only search reuse | Opt-in GTP `reuseRootTree` / `kg-reuse-root-tree` → `root-only-v1`, so allow/avoid changes can keep a first-ply tree | `cpp/search/search.cpp`, `searchexplorehelpers.cpp`, `searchhelpers.cpp`, `searchresults.cpp`, `searchupdatehelpers.cpp`, `cpp/command/gtp.cpp`, `docs/GTP_Extensions.md`, `docs/KG_ROOT_REUSE.md`, `python/probe_kg_root_reuse.py` |
| Windows CUDA 12.8 and 13.2 source bundles | Hash-locked SDK, PE audit, slim ZIP named from the source SHA, receipts retain `PENDING_HARDWARE` | `scripts/windows_cuda_sdk.py`, `scripts/build_windows_cuda_bundle.py`, `scripts/windows_cuda12.lock.json`, `scripts/windows_cuda13.lock.json`, `.github/workflows/windows-cuda-bundles.yml`, `cpp/CMakeLists.txt` (honor an explicit architecture list) |
| CI cost controls | Docs-only skip of the six platform jobs, ccache / vcpkg binary cache, CUDA download cache that still re-hashes inputs | `scripts/ci_scope.py`, `.github/actions/ccache/action.yml`, `.github/workflows/build.yml`, `docs/KG_CI.md` |
| Training-data corrections | Scored-SGF near-end thresholds, Fox handicap-komi fallthrough, per-turn empty Q slots | `cpp/command/writetrainingdata.cpp`, `python/test_fox_sgf_filter.py` |
| Python Board hash | Backport of upstream PR #1256, plus extra capture/undo cases and a small workflow | `python/katago/game/board.py`, `python/tests/test_board_zobrist.py`, `.github/workflows/python-board.yml` |
| Visit-cap test invariant | Terminal leaves have no snapshot to freeze | `cpp/tests/testsearchnonn.cpp` (also the body of PR #5) |

Default `setAvoidMoveUntilByLoc` still clears the tree when restriction arrays change. GTP opts into reuse with `reuseRootTree true`, and reuse requires both old and new masks to be empty or depths 0–1 (`cpp/search/search.cpp` around the `rootOnly` check). Changes involving deeper masks, and normal position/player, komi/rule and `clear_cache` invalidations, still clear; see the full [reuse contract](KG_ROOT_REUSE.md).

## What held up

- **Reuse contract vs code.** Masked root children stay allocated and are dropped from selection, analysis rows, aggregate weight, ownership, and dame-pass suppression. A current-player mask change deletes the root visit-cap snapshot and restarts the counted interval from `rootVisitCapStartVisits`. Opponent-only and empty/all-zero masks leave that interval alone. Symmetry pruning turns off for the rest of the position when the duplicate-location partition changes, and `beginSearch` then stops deleting those children (`cpp/search/search.cpp` symmetry block; `isAllowedRootMove` in `cpp/search/searchhelpers.cpp`). Analysis weight factors use the compacted reported-child index after masked rows are skipped (`cpp/search/searchresults.cpp`, `statsBuf[buf.size()-1]`).
- **Probe fail-closed behavior.** Import rejects `-O` / nonzero `PYTHONOPTIMIZE`. The output directory must be new. `result.json` is written only after the checks, a zero exit, reader joins, and log flushes (`python/probe_kg_root_reuse.py`). Lifecycle failures have a separate unittest.
- **CUDA supply chain on the fork-owned path.** Lock entries require HTTPS and a 64-hex SHA-256. Downloads use `curl --proto =https`. Cache hits call `verify_input` again (`scripts/windows_cuda_sdk.py`). Zip members go through `safe_name`. The executable is rejected if it imports `z.dll`, `zip.dll`, or the dynamic MSVC runtime. `version` is started with `PATH` limited to System32. Receipts set `PENDING_HARDWARE` in code (`scripts/build_windows_cuda_bundle.py`). Workflow permissions are `contents: read`. There is no `pull_request_target`.
- **Architecture lists match.** CMake's CUDA 12.8 default (`cpp/CMakeLists.txt`, the `VERSION_GREATER_EQUAL 12.8` branch) matches `scripts/windows_cuda12.lock.json` `gpuArchitectures`. The CUDA 13 default matches `scripts/windows_cuda13.lock.json`. The bundle also passes `CMAKE_CUDA_ARCHITECTURES` explicitly, and CMake now keeps that list.
- **Hosted evidence on `0feaf24d` (2026-10-06), all success:** [Build and Test](https://github.com/22nsuk/KataGo/actions/runs/37419628825), [Python board](https://github.com/22nsuk/KataGo/actions/runs/37419628780), [Windows CUDA dispatch](https://github.com/22nsuk/KataGo/actions/runs/37419633907), [root-reuse dispatch](https://github.com/22nsuk/KataGo/actions/runs/37419636655). Those CUDA and root-reuse runs are `workflow_dispatch`, not `push`; none is a GPU inference acceptance run.

## Follow-ups and resolved gaps

The items distinguish release gates, process guards, corrections included here, upstream work, and optional hardening. They are not eight outstanding engine defects.

### 1. Release gate — exact release-source validation

**Fork-owned. Manual procedure; no new automatic gate.**

The release-dispatch procedure is in the [CI guide](KG_CI.md#릴리스-소스와-검증-대상).

### 2. Process guard — leave PR #5 on its upstream-review base

**Fork process. The test correction is already on master.**

[PR #5](https://github.com/22nsuk/KataGo/pull/5) targets `codex/upstream-review-base-20261005`, not `master`. Its body identifies the extracted terminal-leaf correction, the existing fork fix, and the reason for a frozen upstream review baseline. Retargeting it here would duplicate already integrated work and mix that baseline with later reuse-test changes. No claim that a fresh merge conflict was reproduced is needed for this decision.

PR #5 also records HTTP 403 (`Resource not accessible by integration`) on upstream submission attempts for its branch and `codex/upstream-near-end-exact`. Resubmit the isolated branches when permissions allow; do not open an upstream PR from fork `master`. No upstream submission or change to PR #5 is part of this documentation PR.

### 3. Corrected — JSON analysis engine is outside the reuse contract

**Fork-owned. Documented in this change, not newly implemented.**

[`analysis.cpp`](https://github.com/22nsuk/KataGo/blob/0feaf24d35f75d6b424b7db3f24063d5a78fa1be/cpp/command/analysis.cpp#L377-L439) calls `bot->setAvoidMoveUntilByLoc(...)` with the default `reuseRootTree = false` and calls `bot->clearSearch()` after processing each request. The JSON expected-key set does not include `reuseRootTree`. Merely passing a reuse flag would not implement persistent reuse across JSON requests; that would need a separately designed request/position ownership and lifetime contract.

GTP parses the option for `kata-analyze`, `kata-genmove_analyze`, `kata-search_analyze`, and `kata-search_analyze_cancellable` (`cpp/command/gtp.cpp`, `isKata` and the `reuseRootTree` key). [KG_ROOT_REUSE.md](KG_ROOT_REUSE.md) now states that JSON analysis is outside this extension.

### 4. Corrected — CI guide and policy include Python board

**Fork-owned. Corrected in this change.**

`docs/KG_CI.md` listed four workflows. [Python board](https://github.com/22nsuk/KataGo/blob/0feaf24d35f75d6b424b7db3f24063d5a78fa1be/.github/workflows/python-board.yml) is a fifth workflow with the same PR-only cancellation rule, its own path filters, Python 3.12, and 7-day JUnit retention. The guide now includes it. `scripts/test_ci_policy.py` adds `python-board` to the existing cancellation-group test; it does not change workflow triggers, permissions, or cancellation behavior.

### 5. Upstream sync watch — preserve Python Board regressions

**Upstream-candidate already on the fork.**

`python/katago/game/board.py` XORs `ZOBRIST_STONE[pla]` for stones being removed. That one-line change matches upstream [PR #1256](https://github.com/lightvector/KataGo/pull/1256), still open when rechecked on 2026-10-08. Both changes add `python/tests/test_board_zobrist.py`; preserve the fork's additional chain-capture, both-color, and pass cases when reconciling that file. Future conflict details depend on the upstream version actually merged, not on this snapshot alone.

### 6. Upstream candidates already fixed here, absent from the pinned upstream snapshot

Send these as their own upstream PRs. They are not reasons to submit the reuse or CUDA work upstream.

| Item | Evidence on the fork | Upstream home |
| --- | --- | --- |
| Visit-cap terminal leaves | `cpp/tests/testsearchnonn.cpp`; PR #5 body | Isolated branch `codex/upstream-terminal-visit-cap` |
| Scored-SGF near-end ratios | `isNearEndByOwnership` in `cpp/command/writetrainingdata.cpp`; `Tests::runTrainingDataEndTests` from `cpp/command/runtests.cpp` | Branch `codex/upstream-near-end-exact` (`90126b1`) |
| Fox handicap-1 / komi mismatch fallthrough | `reportSgfDone(..., "GameHandicap1MismatchKomi"); return;` in `writetrainingdata.cpp` | No isolated branch found in the original review. Needs its own upstream patch. `python/test_fox_sgf_filter.py` is the fork regression |
| Empty per-turn Q slots for human SGFs | `whiteQValueTargets(whiteValueTargets.size())` before `addRow` in `writetrainingdata.cpp`; NPZ check in `python/test_fox_sgf_filter.py` (`qValueTargetsNCMove` all zeros) | Same Fox patch is a reasonable bundle |

`trainingwrite.cpp` asserts `whiteValueTargetsIdx < whiteQValueTargets.size()`. The old empty vector failed that assert on SGF rows. The fork sizes the vector and still writes no searched Q values.

### 7. Optional — remaining floating Actions and TheRock integrity

**Mostly inherited from upstream. Fork-owned workflows are already pinned.**

`Build and Test` and `ONNX backend` still use `actions/upload-artifact@v4` (and ONNX helpers use `actions/checkout@v4` / `actions/cache@v4`). New fork workflows pin `actions/upload-artifact` to `ea165f8d65b6e75b540449e92b4886f43607fa02` and `actions/cache` to `0057852bfaa89a56745cba8c7296529d2fc39830`. The Linux OpenCL upload the fork rewired (`build.yml` "Upload artifact") is still `@v4`.

The Windows ROCm job downloads the TheRock tarball by URL and version with no SHA-256. `docs/KG_CI.md` does not promise a new hash guarantee for TheRock or ONNX provider caches. These are separate hardening candidates, not changes made or blockers established by this PR.

### 8. Optional — validate lockfile runtime names as filenames

**Fork-owned input-validation hardening. No current-bundle exploit established.**

[`windows_cuda_sdk.py`](https://github.com/22nsuk/KataGo/blob/0feaf24d35f75d6b424b7db3f24063d5a78fa1be/scripts/windows_cuda_sdk.py) validates archive member paths with `safe_name`, but `load_lock` only checks `runtimeFiles` for case-insensitive duplicates, not single-filename shape. In the `baseline-runtime` path, names beginning with `msvcp` or `vcruntime` are passed to `copy_member` as archive member names and appended to `prefix / "runtime"`. A traversal write would additionally require a matching archive member; the checked-in, hash-locked inputs are not shown to provide one. Rejecting non-filename entries at lock load would make the intended contract explicit and fail earlier. Do not describe the missing shape check alone as a demonstrated escape with current bundles.

## Upstream-merge risk

The reviewed fork already contains the pinned upstream snapshot. Likely conflict areas in a later sync are the search files above, `cpp/command/gtp.cpp`, `cpp/command/writetrainingdata.cpp`, `cpp/CMakeLists.txt`, and `.github/workflows/build.yml` / `onnx-backend.yml`; inspect the actual future diff rather than assuming it is a no-op.

Keep reuse and the CUDA bundle recipe on this fork. The reviewed upstream source does not advertise `kg-reuse-root-tree`; a stock binary without that capability cannot satisfy KG-next's reuse negotiation ([CUDA 13 notes](KG_CUDA13_WINDOWS.md)).

CMake's user-architecture guard exists so a KG-next single-GPU or lockfile architecture list is not replaced by the version default (`cpp/CMakeLists.txt`, `_katago_cuda_architectures_given` before `enable_language(CUDA)`). That guard is fork-specific and should stay with the bundle recipe.

## Limits

- The linked hosted jobs are not NVIDIA GPU inference runs. `runtests`, `runoutputtests`, PE import audits, and `version` do not prove GPU inference.
- Bundle receipts hard-code `hardwareAcceptanceStatus: PENDING_HARDWARE` and a reason string in `scripts/build_windows_cuda_bundle.py`. Publishing a release or updating the KG-next catalog is a separate step (`README.md`, `docs/KG_WINDOWS_CUDA_BUNDLES.md`).
- Release `kg-next-0feaf24d35f7` reports a local RTX 4090 / driver 610.88 smoke: both exact executables, 39 root-reuse cases each, model `g170-b6c96`, White, four threads, ownership, root visit cap 32. The release text calls that scoped smoke, not full acceptance. This review did not reproduce it or independently validate its raw hardware logs.
- The manual CUDA 13 recipe (`docs/KG_CUDA13_WINDOWS.md`, installed toolkit, CMake 4.1.3 or newer, one architecture) is different from the hash-locked CI bundles (CMake 3.31.6, Ninja, both lockfile architecture lists).
- CPU Eigen probe success does not transfer to the Windows CUDA ZIP. The CUDA workflow does not run `python/probe_kg_root_reuse.py`, because that probe initializes the network.
- Slim ZIPs omit NVIDIA runtime DLLs and models on purpose.
- Fox SGF tests use synthetic 30-move records (`python/test_fox_sgf_filter.py`). They do not certify every real Fox handicap file.
