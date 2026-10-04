#!/usr/bin/env python3
"""Build an exact engine revision, audit its portable runtime, and seal a slim ZIP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import zipfile

import windows_cuda_sdk as sdk

SOURCE_REPOSITORY = "https://github.com/22nsuk/KataGo"
SYSTEM_LIBRARIES = {
    "kernel32.dll", "advapi32.dll", "user32.dll", "gdi32.dll", "shell32.dll", "ole32.dll",
    "oleaut32.dll", "ws2_32.dll", "bcrypt.dll", "crypt32.dll", "ntdll.dll", "secur32.dll",
    "version.dll", "cfgmgr32.dll", "shlwapi.dll", "setupapi.dll", "dbghelp.dll", "nvcuda.dll",
}


def source_check(source: Path, expected_sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("A full immutable source SHA is required")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                     cwd=source, text=True)
    if head != expected_sha or status:
        raise ValueError("Source checkout must be clean and equal the requested SHA")


def engine_options(prefix: Path, lock: dict) -> list[str]:
    return ["-DCMAKE_BUILD_TYPE=Release", "-DUSE_BACKEND=CUDA", "-DBUILD_DISTRIBUTED=0",
            "-DNO_GIT_REVISION=0", "-DKATAGO_AUTO_FETCH_DEPS=OFF", "-DUSE_AVX2=OFF",
            "-DUSE_TCMALLOC=OFF", "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
            "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
            "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
            f"-DCMAKE_PREFIX_PATH={prefix}", f"-DCMAKE_FIND_ROOT_PATH={prefix}",
            "-DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY", "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY",
            "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY", "-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER",
            f"-DCMAKE_CUDA_COMPILER={prefix / 'cuda/bin/nvcc.exe'}",
            f"-DCUDAToolkit_ROOT={prefix / 'cuda'}",
            "-DCMAKE_CUDA_ARCHITECTURES=" + ";".join(str(a) for a in lock["gpuArchitectures"]),
            "-DCMAKE_CUDA_FLAGS=-Wno-deprecated-gpu-targets",
            f"-DCUDNN_INCLUDE_DIR={prefix / 'cudnn/include'}",
            f"-DCUDNN_LIBRARY={prefix / 'cudnn/lib/x64/cudnn.lib'}",
            f"-DZLIB_INCLUDE_DIR={prefix / 'include'}", f"-DZLIB_LIBRARY={prefix / 'lib/zlibstatic.lib'}",
            f"-DLIBZIP_INCLUDE_DIR_ZIP={prefix / 'include'}",
            f"-DLIBZIP_INCLUDE_DIR_ZIPCONF={prefix / 'include'}",
            f"-DLIBZIP_LIBRARY={prefix / 'lib/zip.lib'};{prefix / 'lib/zlibstatic.lib'}"]


def inspect_pe(headers: str, dependencies: str, bundled: set[str], *, executable: bool = False) -> dict:
    if not re.search(r"8664 machine \(x64\)", headers, re.IGNORECASE):
        raise ValueError("Expected native PE/x64")
    needed = sorted(set(re.findall(r"(?im)^\s*([A-Za-z0-9_.-]+\.dll)\s*$", dependencies)))
    if not needed:
        raise ValueError("Missing PE import evidence")
    allowed = SYSTEM_LIBRARIES | {name.lower() for name in bundled}
    for name in needed:
        lower = name.lower()
        if lower not in allowed and not lower.startswith("api-ms-win-"):
            raise ValueError(f"Unapproved PE dependency: {name}")
        if executable and (lower in {"z.dll", "zlib.dll", "zlib1.dll", "zip.dll"}
                           or lower.startswith(("msvcp", "vcruntime"))):
            raise ValueError(f"Executable did not statically link its libraries/runtime: {name}")
    return {"needed": needed}


def portable_environment() -> dict[str, str]:
    env = {name.upper(): value for name, value in os.environ.items()}
    if not env.get("SYSTEMROOT"):
        raise ValueError("Windows system directory is unavailable")
    env["PATH"] = str(Path(env["SYSTEMROOT"]) / "System32")
    return env


def copy_licenses(source: Path, prefix: Path, output: Path) -> None:
    files = [source / "LICENSE"] + [p for p in (source / "cpp/external").rglob("*")
                                      if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))]
    for path in files:
        destination = output / "licenses/KataGo" / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    shutil.copytree(prefix / "share/licenses", output / "licenses/dependencies")


def seal_archive(path: Path, bundle: Path, receipt: dict) -> None:
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in receipt["files"]:
            info = zipfile.ZipInfo(item["file"], (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with (bundle / item["file"]).open("rb") as src, archive.open(info, "w") as dest:
                shutil.copyfileobj(src, dest, 1024 * 1024)
        info = zipfile.ZipInfo("source-release.json", (1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def verify_archive(path: Path, target: str, source_sha: str) -> dict:
    import hashlib
    with zipfile.ZipFile(path) as archive:
        names = [str(sdk.safe_name(item.filename)) for item in archive.infolist()]
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Archive has duplicate names")
        receipt = json.loads(archive.read("source-release.json"))
        if (receipt.get("sourceCommit") != source_sha or receipt.get("target") != target
                or receipt.get("sourceRepository") != SOURCE_REPOSITORY or receipt.get("backend") != "CUDA"
                or receipt.get("origin") != "project-source-build"
                or any(receipt.get(key) != "PASS" for key in ("buildStatus", "packagingStatus", "dependencyAuditStatus"))):
            raise ValueError("Archive source identity or required build gates differ")
        expected = {}
        for item in receipt.get("files", []):
            name = str(sdk.safe_name(item["file"]))
            if name.casefold() in {n.casefold() for n in expected}:
                raise ValueError("Duplicate inventory entry")
            expected[name] = item
        if (set(names) != set(expected) | {"source-release.json"}
                or not {"katago.exe", "default_gtp.cfg", "analysis_example.cfg"} <= expected.keys()
                or not any(n.startswith("licenses/") for n in expected)
                or receipt.get("executable") != expected.get("katago.exe")):
            raise ValueError("Archive inventory is incomplete")
        for name, item in expected.items():
            info = archive.getinfo(name)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Archive contains a symlink")
            with archive.open(name) as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            if info.file_size != item["sizeBytes"] or sha != item["sha256"]:
                raise ValueError(f"Archive file was modified: {name}")
    return receipt


def build(source: Path, output: Path, cache: Path, target: str, source_sha: str, jobs: int,
          sdk_prefix: Path | None = None) -> dict:
    source, output, cache = source.resolve(), output.resolve(), cache.resolve()
    if output.is_relative_to(source) or cache.is_relative_to(source):
        raise ValueError("Build output and download cache must be outside the source checkout")
    source_check(source, source_sha)
    lock = sdk.load_lock(target)
    sdk.host_check(lock)
    output.mkdir(parents=True, exist_ok=False)
    receipt = dict(schemaVersion=1, sourceRepository=SOURCE_REPOSITORY, sourceCommit=source_sha,
                   origin="project-source-build", target=target, backend="CUDA", experimental=False,
                   runtimeProfile=lock["runtimeProfile"], zlibLinkage="static",
                   buildStatus="FAIL", packagingStatus="NOT_RUN", dependencyAuditStatus="NOT_RUN",
                   hardwareAcceptanceStatus="PENDING_HARDWARE",
                   hardwareAcceptanceReason="GitHub hosted Windows runners have no NVIDIA GPU; version and native CPU tests do not prove GPU inference.",
                   dependencyLockSha256=sdk.digest(sdk.PROFILES[target]),
                   dependencies=lock["dependencies"] + lock["sourceLibraries"])
    try:
        prefix = sdk_prefix.resolve() if sdk_prefix else sdk.build_sdk(output / "sdk", cache, target, jobs)
        verified_sdk = sdk.verify_sdk(prefix, target)
        receipt["sdkReceipt"] = sdk.record(prefix / "sdk-receipt.json", prefix)
        receipt["toolchain"] = {"msvcToolset": verified_sdk["msvcToolset"],
                                "windowsSdk": verified_sdk["windowsSdk"],
                                "nvccVersionOutput": verified_sdk["nvccVersionOutput"],
                                "cmakeVersion": subprocess.check_output(["cmake", "--version"], text=True),
                                "ninjaVersion": subprocess.check_output(["ninja", "--version"], text=True)}
        build_dir = output / "build"
        options = engine_options(prefix, lock)
        receipt["configuration"] = options
        with (output / "build.log").open("w", encoding="utf-8") as log:
            for command in (["cmake", "-S", str(source / "cpp"), "-B", str(build_dir), "-G", "Ninja", *options],
                            ["cmake", "--build", str(build_dir), "--target", "katago", "--parallel", str(jobs)]):
                subprocess.run(command, check=True, env=sdk.environment(prefix), stdout=log, stderr=subprocess.STDOUT)
        source_check(source, source_sha)
        compiler_files = list((build_dir / "CMakeFiles").glob("*/CMakeCXXCompiler.cmake"))
        if len(compiler_files) != 1:
            raise ValueError("Missing or ambiguous C++ compiler identity")
        compiler = compiler_files[0].read_text(encoding="utf-8")
        receipt["compiler"] = {key: re.search(r'set\(' + key + r' "([^\"]+)"\)', compiler).group(1)
                               for key in ("CMAKE_CXX_COMPILER_ID", "CMAKE_CXX_COMPILER_VERSION", "CMAKE_CXX_COMPILER")}
        portable = output / "portable"
        portable.mkdir()
        shutil.copy2(build_dir / "katago.exe", portable / "katago.exe")
        for name in lock["runtimeFiles"]:
            os.link(prefix / "runtime" / name, portable / name)
        audits = {}
        for path in sorted(portable.iterdir()):
            headers, dependencies = [subprocess.check_output(["dumpbin", flag, str(path)], text=True)
                                     for flag in ("/headers", "/dependents")]
            audits[path.name] = inspect_pe(headers, dependencies, set(lock["runtimeFiles"]),
                                           executable=path.name == "katago.exe")
        version = subprocess.run([str(portable / "katago.exe"), "version"], check=True, cwd=portable,
                                 env=portable_environment(), capture_output=True, text=True, timeout=30).stdout
        if f"Git revision: {source_sha}" not in version or "Using CUDA backend" not in version:
            raise ValueError("Portable executable reports the wrong source or backend")
        for command in ("runtests", "runoutputtests"):
            with (output / (command + ".log")).open("w", encoding="utf-8") as log:
                subprocess.run([str(portable / "katago.exe"), command], check=True, cwd=source / "cpp",
                               env=portable_environment(), stdout=log, stderr=subprocess.STDOUT, timeout=600)
        bundle = output / "release"
        bundle.mkdir()
        shutil.copy2(portable / "katago.exe", bundle / "katago.exe")
        shutil.copy2(source / "cpp/configs/gtp_example.cfg", bundle / "default_gtp.cfg")
        shutil.copy2(source / "cpp/configs/analysis_example.cfg", bundle / "analysis_example.cfg")
        copy_licenses(source, prefix, bundle)
        receipt.update(buildStatus="PASS", packagingStatus="PASS", dependencyAuditStatus="PASS",
                       nativeTests={"runtests": "PASS", "runoutputtests": "PASS"}, versionOutput=version,
                       executable=sdk.record(bundle / "katago.exe", bundle), files=sdk.inventory(bundle),
                       peAudit=audits, auditedRuntimeFiles=[sdk.record(portable / n, portable) for n in lock["runtimeFiles"]])
        name = f"katago-source-{source_sha[:12]}-{target}.zip"
        archive = output / name
        seal_archive(archive, bundle, receipt)
        verify_archive(archive, target, source_sha)
        metadata_file = bundle / "source-release.json"
        metadata_file.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        summary = dict(sourceCommit=source_sha, sourceRepository=SOURCE_REPOSITORY, target=target,
                       runtimeProfile=lock["runtimeProfile"], archive=sdk.record(archive, output),
                       executable=receipt["executable"], sourceMetadata=sdk.record(metadata_file, bundle),
                       hardwareAcceptanceStatus=receipt["hardwareAcceptanceStatus"],
                       releaseTag="kg-next-" + source_sha[:12])
        (output / "artifact-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        (output / "source-package.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--target", choices=sdk.PROFILES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--sdk", type=Path, help="Reuse a sealed SDK after rechecking its receipt and every file")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error("jobs must be between 1 and 64")
    print(json.dumps(build(args.source, args.output, args.cache, args.target, args.source_sha, args.jobs, args.sdk), indent=2))


if __name__ == "__main__":
    main()
