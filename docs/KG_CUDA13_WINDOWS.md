# Windows CUDA 13 build profile

For automated hash-locked CUDA 12.8 and 13.2 release builds, use the
[Windows CUDA source bundle workflow](KG_WINDOWS_CUDA_BUNDLES.md). The manual
recipe below remains useful for a locally installed toolkit and a selected GPU.

Build this engine revision directly with its CMake project. Root reuse does not
need a GUI-owned source patch. The CUDA backend uses cuBLAS, cuDNN, and NVRTC; the
optional CUTLASS fused FFN path is disabled automatically under MSVC.

## Fixed inputs

For a repeatable CUDA 13 profile, select and record these dependency versions:

| Input | Version used in this profile |
| --- | --- |
| CUDA Toolkit | 13.2.2, CUDA components 13.2.86, x64 Windows |
| cuDNN | 9.24.0.43, x64 Windows package for CUDA 13 |
| Host compiler | Visual Studio 2022, MSVC v143 14.44, x64 |
| CMake | 4.1.3 or an explicitly recorded newer version |
| zlib | 1.3.1, commit `51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf` |
| Engine | The exact commit SHA checked out from this repository |

Install the selected CUDA, cuDNN, Visual Studio C++ toolset, and CMake first.
Keep their original package filenames and checksums with the build record. Use a
host compiler supported by the selected toolkit; do not enable nvcc's
`allow-unsupported-compiler` option. This profile is an explicit recipe, not a
claim that every dependency version has been built or run by this repository's
CPU CI.

## PowerShell 7 build

Start in the engine repository. Adjust the dependency locations, engine revision,
and architecture to match the intended release. `89` below builds for an Ada GPU;
use an explicit semicolon-separated set for a release supporting multiple GPUs.
CMake preserves an explicitly provided `CMAKE_CUDA_ARCHITECTURES` or `CUDAARCHS`.

```powershell
$engineRevision = '<full-engine-commit-sha>'
$cudaRoot = 'C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.2'
$cudnnRoot = 'C:/deps/cudnn-9.24.0.43-cuda13'
$buildRoot = Join-Path (Get-Location) 'work/cuda13-build'
$dependencyRoot = Join-Path (Get-Location) 'work/cuda13-deps'
$zlibSource = Join-Path $dependencyRoot 'zlib'
$zlibBuild = Join-Path $dependencyRoot 'zlib-build'
$zlibInstall = Join-Path $dependencyRoot 'zlib-install'

git checkout --detach $engineRevision
if ($LASTEXITCODE -ne 0) { throw 'Engine checkout failed' }
git clone https://github.com/madler/zlib.git $zlibSource
if ($LASTEXITCODE -ne 0) { throw 'zlib clone failed' }
git -C $zlibSource checkout --detach 51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf
if ($LASTEXITCODE -ne 0) { throw 'zlib checkout failed' }
cmake -S $zlibSource -B $zlibBuild -G 'Visual Studio 17 2022' -A x64 `
  -T 'v143,version=14.44' "-DCMAKE_INSTALL_PREFIX=$zlibInstall"
if ($LASTEXITCODE -ne 0) { throw 'zlib configure failed' }
cmake --build $zlibBuild --config Release --parallel 4
if ($LASTEXITCODE -ne 0) { throw 'zlib build failed' }
cmake --install $zlibBuild --config Release
if ($LASTEXITCODE -ne 0) { throw 'zlib install failed' }

cmake -S cpp -B $buildRoot -G 'Visual Studio 17 2022' -A x64 `
  -T "v143,version=14.44,cuda=$cudaRoot" `
  -DUSE_BACKEND=CUDA -DNO_LIBZIP=1 -DBUILD_DISTRIBUTED=0 `
  -DCMAKE_CUDA_ARCHITECTURES=89 `
  "-DCUDAToolkit_ROOT=$cudaRoot" `
  "-DCUDNN_INCLUDE_DIR=$cudnnRoot/include" `
  "-DCUDNN_LIBRARY=$cudnnRoot/lib/x64/cudnn.lib" `
  "-DZLIB_INCLUDE_DIR=$zlibInstall/include" `
  "-DZLIB_LIBRARY=$zlibInstall/lib/zlibstatic.lib"
if ($LASTEXITCODE -ne 0) { throw 'Engine configure failed' }
cmake --build $buildRoot --config Release --parallel 4
if ($LASTEXITCODE -ne 0) { throw 'Engine build failed' }
```

Use a fresh build directory when changing toolkit, host toolset, backend, or
architecture profile. The cuDNN archive layout may differ: point the include and
library arguments at the actual `cudnn.h` and `cudnn.lib` locations. CUDA 13 builds
require a GPU architecture that the selected toolkit supports.

## Runtime and verification

The executable is normally `work/cuda13-build/Release/katago.exe`. Run it with the
selected CUDA and cuDNN runtime DLL directories on `PATH`. For a portable package,
include the runtime DLLs required by that executable and the CUDA/cuDNN package
license notices, together with the engine's `LICENSE`. Preserve the full cuDNN
runtime directory because `cudnn64_9.dll` loads additional component DLLs. Check
redistribution terms for the selected vendor packages before publishing them.

```powershell
$env:PATH = "$cudaRoot/bin;$cudnnRoot/bin;$env:PATH"
$engine = Join-Path $buildRoot 'Release/katago.exe'
& $engine version
if ($LASTEXITCODE -ne 0) { throw 'Engine cannot start' }
python python/probe_kg_root_reuse.py --engine $engine `
  --model cpp/tests/models/g170-b6c96-s175395328-d26788732.bin.gz `
  --output work/cuda13-proof --player W --threads 4 --ownership --root-visit-cap 32
if ($LASTEXITCODE -ne 0) { throw 'Native CUDA root-reuse proof failed' }
Get-FileHash -Algorithm SHA256 $engine
```

The GTP probe actually initializes the selected model and executes GPU searches.
`version` alone proves only that the process can start. Repeat the probe for both
colors and one/four threads as in the CPU workflow. Record the engine SHA,
`nvcc --version`, cuDNN version macros, MSVC toolset, CMake version, GPU/driver,
architecture list, executable/runtime hashes, and proof logs. A stock upstream
CUDA executable can be used for ordinary analysis, but it cannot advertise this
fork's `root-only-v1` capability.
