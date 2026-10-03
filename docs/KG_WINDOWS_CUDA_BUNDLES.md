# Windows CUDA source bundles

The `Windows CUDA source bundles` workflow builds two Windows x64 CUDA profiles
from the actual pull request head commit, or the selected `workflow_dispatch`
commit. It uploads artifacts for review and does not publish a GitHub release.

| Asset target | CUDA toolkit | cuDNN | Architectures |
| --- | --- | --- | --- |
| `windows-nvidia` | 12.8.0, nvcc 12.8.61 | 9.8.0.87 | 50, 52, 53, 60, 61, 62, 70, 72, 75, 80, 86, 87, 90, 120 |
| `windows-nvidia-cuda13` | 13.2.2, nvcc 13.2.86 | 9.24.0.43 | 75, 80, 86, 87, 88, 89, 90, 100, 110, 103, 120, 121 |

The two `scripts/windows_cuda*.lock.json` files own the exact NVIDIA package
URLs, sizes, and SHA-256 values. CUDA 13 requires the separate `cuda_crt` and
`libnvvm` compiler components and keeps CCCL headers under `include/cccl`.
The installer handles both NVIDIA's `bin/` and `bin/x64/` runtime layouts.
zlib 1.3.1 and libzip 1.11.4 are built from hash-locked source using the static
MSVC runtime (`/MT`); the engine executable must not import `z.dll`, `zip.dll`,
or the dynamic MSVC runtime. NVIDIA DLLs retain their own vendor dependencies.

CI uses Windows Server 2022, the MSVC 14.44 x64 toolset, Python 3.12, CMake
3.31.6, and Ninja 1.11.1.3. The receipt records the full installed compiler and
Windows SDK versions. These inputs provide a reproducible build recipe; they
do not promise identical executable bytes across changes to the hosted runner,
Windows SDK, or compiler patch version.

## Local build

Start PowerShell 7 with an MSVC 14.44 x64 developer environment, and make Python
3.12 or newer, CMake 3.31.6, Ninja, `curl.exe`, and `dumpbin.exe` available on
`PATH`. The CUDA toolkit does not need to be installed system-wide. Use a clean
checkout and place output and cache directories outside it.

```powershell
$sourceSha = git rev-parse HEAD
$buildRoot = 'C:/katago-builds/windows-nvidia-cuda13'
$cacheRoot = 'C:/katago-builds/downloads'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B scripts/build_windows_cuda_bundle.py --source . --source-sha $sourceSha `
  --target windows-nvidia-cuda13 --output $buildRoot --cache $cacheRoot --jobs 2
if ($LASTEXITCODE -ne 0) { throw 'CUDA build or package verification failed' }
```

Use a fresh output directory for every build. Downloads may be reused only
after rechecking their locked hashes and sizes. `--sdk <prefix>` reuses a
previous SDK only after validating its lock identity and every SDK file.
The SDK and portable staging directory use hard links on the same local
filesystem to avoid duplicate copies of large runtime DLLs.

## Evidence and release contract

The builder assembles a full portable runtime, inspects PE/x64 headers and the
imports of the executable and every vendor DLL, and rejects imports outside
the Windows/NVIDIA-driver allowlist or declared bundle. It starts `version`
with only the Windows system directory on `PATH`, checks the embedded full
source SHA and CUDA backend, and runs `runtests` and `runoutputtests` from the
source test directory. CUDA/cuDNN and MSVC hashes, compiler identity, and
runtime import audits are retained in `source-package.json`. Failures keep
their diagnostic receipt and logs; no verified release artifact is uploaded.

The release ZIP is deliberately small because KG-next installs its separately
locked NVIDIA runtime. The names are:

```text
katago-source-<source-sha-first-12>-windows-nvidia.zip
katago-source-<source-sha-first-12>-windows-nvidia-cuda13.zip
```

Each ZIP contains `katago.exe`, `default_gtp.cfg`, `analysis_example.cfg`,
`licenses/**`, and `source-release.json`. The manifest has `schemaVersion: 1`,
`origin: project-source-build`, `sourceRepository:
https://github.com/22nsuk/KataGo`, the actual full `sourceCommit`, the target,
`backend: CUDA`, and the exact `runtimeProfile`. Its `files` list covers every
other archive member with relative filename, size, and SHA-256. `executable`
binds the engine to that list. The separate `auditedRuntimeFiles` and `peAudit`
fields describe the full runtime checked before trimming; those DLLs are not
members of the slim ZIP. The configs are copied from the same engine commit.

`artifact-summary.json` supplies the archive/executable/manifest sizes and
SHA-256 values for KG-next's per-asset catalog. Its suggested immutable release
tag is `kg-next-<source-sha-first-12>`. A release should contain artifacts from
one exact SHA. Publishing, GPU acceptance, and GUI catalog promotion remain
separate explicit steps.

Hosted CI has no NVIDIA GPU. Both manifests therefore report
`hardwareAcceptanceStatus: PENDING_HARDWARE` and an explicit reason. CPU tests,
PE inspection, and `version` are not GPU inference evidence. Run the real
`python/probe_kg_root_reuse.py` probe on a supported NVIDIA GPU, with the locked
runtime and test model, for both colors and one/four threads before claiming
GPU acceptance. Preserve executable hash, GPU/driver, profile, and probe logs
as described in [KG_CUDA13_WINDOWS.md](KG_CUDA13_WINDOWS.md).

## Build helper checks

```powershell
python -B -m unittest discover -s scripts -p test_windows_cuda_bundle.py -v
```

These checks reject modified dependencies, unsafe/colliding archive paths,
missing DLL imports, dynamic zlib/MSVC imports, mismatched source identities,
incomplete file inventories, and modified ZIP members. They also cover both
official runtime layouts and deterministic ZIP construction for fixed inputs.
