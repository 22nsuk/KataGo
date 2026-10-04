#!/usr/bin/env python3
"""Build and verify the hash-locked SDK for the Windows CUDA release profiles."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile
from urllib.parse import urlparse
import zipfile

PROFILES = {
    "windows-nvidia": Path(__file__).with_name("windows_cuda12.lock.json"),
    "windows-nvidia-cuda13": Path(__file__).with_name("windows_cuda13.lock.json"),
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record(path: Path, root: Path) -> dict:
    return {"file": path.relative_to(root).as_posix(), "sizeBytes": path.stat().st_size,
            "sha256": digest(path)}


def inventory(root: Path, exclude: str = "sdk-receipt.json") -> list[dict]:
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"File escapes inventory root: {path}")
        if path.is_file() and path.relative_to(root).as_posix() != exclude:
            result.append(record(path, root))
    return result


def load_lock(target: str) -> dict:
    lock = json.loads(PROFILES[target].read_text(encoding="utf-8"))
    if (lock.get("schemaVersion") != 1 or lock.get("target") != target
            or lock.get("provider") != "cuda" or lock.get("msvcToolset") != "14.44"):
        raise ValueError("Unexpected Windows CUDA dependency lock")
    names = set()
    for item in lock["dependencies"] + lock["sourceLibraries"]:
        if (item["name"] in names or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or urlparse(item["url"]).scheme != "https"):
            raise ValueError("Dependency lock contains duplicate names or invalid integrity metadata")
        names.add(item["name"])
    if len(set(name.casefold() for name in lock["runtimeFiles"])) != len(lock["runtimeFiles"]):
        raise ValueError("Dependency lock contains duplicate runtime filenames")
    return lock


def host_check(lock: dict) -> None:
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("This build requires native Windows/x86_64")
    if (not os.environ.get("VCToolsVersion", "").startswith(lock["msvcToolset"] + ".")
            or os.environ.get("VSCMD_ARG_TGT_ARCH") != "x64"):
        raise ValueError("Run from the locked MSVC 14.44 x64 developer environment")


def environment(prefix: Path) -> dict[str, str]:
    env = {name.upper(): value for name, value in os.environ.items()}
    removed = {"CMAKE_PREFIX_PATH", "CMAKE_TOOLCHAIN_FILE", "CMAKE_LIBRARY_PATH",
               "CMAKE_INCLUDE_PATH", "CPATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH",
               "CFLAGS", "CXXFLAGS", "LDFLAGS", "PKG_CONFIG_PATH", "CL", "_CL_",
               "LINK", "_LINK_", "CUDACXX", "CUDAHOSTCXX", "CUDAFLAGS", "CUDAARCHS",
               "NVCC_PREPEND_FLAGS", "NVCC_APPEND_FLAGS"}
    for name in list(env):
        if name in removed or name.startswith("CUDA_PATH"):
            env.pop(name)
    env["PATH"] = os.pathsep.join((str(prefix / "runtime"), str(prefix / "cuda/bin"), env["PATH"]))
    return env


def download(item: dict, cache: Path, log) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / (item["sha256"] + "-" + Path(urlparse(item["url"]).path).name)
    if not archive.is_file():
        pending = archive.with_name(archive.name + ".part")
        subprocess.run(["curl.exe", "--fail", "--location", "--proto", "=https",
                        "--proto-redir", "=https", "--retry", "3", "--connect-timeout", "30",
                        "--max-time", "1800", "--output", str(pending), item["url"]],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
        verify_input(pending, item)
        pending.replace(archive)
    verify_input(archive, item)
    return archive


def verify_input(path: Path, item: dict) -> None:
    if (not path.is_file() or digest(path) != item["sha256"]
            or ("sizeBytes" in item and path.stat().st_size != item["sizeBytes"])):
        raise ValueError(f"Dependency size or SHA-256 mismatch: {item['name']}")


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or "\\" in name or str(path) != name.rstrip("/")
            or any(part in {".", ".."} or ":" in part or part.endswith((".", " "))
                   for part in name.rstrip("/").split("/"))):
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def zip_entries(bundle: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    entries, roots, seen = [], set(), set()
    for item in bundle.infolist():
        path = safe_name(item.filename)
        key = str(path).casefold()
        if key in seen or (item.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"Duplicate path or symlink in archive: {item.filename}")
        seen.add(key)
        roots.add(path.parts[0])
        if not item.is_dir():
            if len(path.parts) < 2:
                raise ValueError("CUDA archive requires one directory root")
            entries.append((item, PurePosixPath(*path.parts[1:])))
    if len(roots) != 1 or not entries:
        raise ValueError("CUDA archive requires one directory root")
    return entries


def copy_member(bundle: zipfile.ZipFile, member, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError(f"Dependency would overwrite an existing file: {target}")
    with bundle.open(member) as src, target.open("xb") as dest:
        shutil.copyfileobj(src, dest, 1024 * 1024)


def install_cuda(archive: Path, prefix: Path, item: dict, runtime: set[str]) -> None:
    with zipfile.ZipFile(archive) as bundle:
        licenses = []
        for member, relative in zip_entries(bundle):
            if relative.parts[0].upper().startswith(("LICENSE", "NOTICE", "EULA")):
                target = prefix / "share/licenses" / item["name"] / relative
                licenses.append(target)
            elif relative.parts[0] in {"include", "lib", "bin", "nvvm"}:
                target = prefix / item["destination"] / relative
            else:
                continue
            copy_member(bundle, member, target)
            # Compiler DLLs under nvvm/ are SDK inputs, never portable runtime members.
            if relative.parts[0] == "bin" and relative.suffix.lower() == ".dll":
                if relative.parent not in {PurePosixPath("bin"), PurePosixPath("bin/x64")} or relative.name not in runtime:
                    raise ValueError(f"Undeclared runtime DLL: {relative}")
                runtime_target = prefix / "runtime" / relative.name
                runtime_target.parent.mkdir(parents=True, exist_ok=True)
                os.link(target, runtime_target)
        if not licenses:
            raise ValueError(f"Redistribution notice missing: {item['name']}")


def library_options(prefix: Path) -> list[str]:
    return ["-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF", "-DBUILD_TESTING=OFF",
            "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW", "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
            "-DCMAKE_INSTALL_LIBDIR=lib", f"-DCMAKE_INSTALL_PREFIX={prefix}",
            f"-DCMAKE_PREFIX_PATH={prefix}", "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF",
            "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF"]


def build_library(archive: Path, item: dict, output: Path, prefix: Path, jobs: int, log) -> None:
    destination = output / (item["name"] + "-source")
    destination.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir() or any(p.is_symlink() for p in destination.rglob("*")):
        raise ValueError("Dependency source requires one directory root without symlinks")
    source, build = roots[0], output / (item["name"] + "-build")
    options = library_options(prefix) + item["cmake"]
    if item["name"] == "libzip":
        options += [f"-DZLIB_INCLUDE_DIR={prefix / 'include'}",
                    f"-DZLIB_LIBRARY={prefix / 'lib/zlibstatic.lib'}"]
    for command in (["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja", *options],
                    ["cmake", "--build", str(build), "--parallel", str(jobs)],
                    ["cmake", "--install", str(build)]):
        subprocess.run(command, check=True, env=environment(prefix), stdout=log, stderr=subprocess.STDOUT)
    notices = [p for p in source.iterdir() if p.is_file()
               and p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))]
    if not notices:
        raise ValueError(f"Redistribution notice missing: {item['name']}")
    for path in notices:
        target = prefix / "share/licenses" / item["name"] / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def verify_sdk(prefix: Path, target: str) -> dict:
    receipt = json.loads((prefix / "sdk-receipt.json").read_text(encoding="utf-8"))
    if (receipt.get("status") != "PASS" or receipt.get("target") != target
            or receipt.get("lockSha256") != digest(PROFILES[target])
            or not receipt.get("files") or receipt["files"] != inventory(prefix)):
        raise ValueError("SDK receipt or files changed after the locked build")
    return receipt


def build_sdk(output: Path, cache: Path, target: str, jobs: int) -> Path:
    lock = load_lock(target)
    host_check(lock)
    output.mkdir(parents=True, exist_ok=False)
    prefix = output / "prefix"
    prefix.mkdir()
    receipt = dict(schemaVersion=1, status="FAIL", target=target, provider="cuda",
                   lockSha256=digest(PROFILES[target]), msvcToolset=os.environ["VCToolsVersion"],
                   windowsSdk=os.environ.get("WindowsSDKVersion", ""))
    try:
        with (output / "build.log").open("w", encoding="utf-8") as log:
            items = lock["dependencies"] + lock["sourceLibraries"]
            archives = {item["name"]: download(item, cache, log) for item in items}
            for item in lock["dependencies"]:
                archive = archives[item["name"]]
                if item["name"] == "baseline-runtime":
                    with zipfile.ZipFile(archive) as bundle:
                        for name in lock["runtimeFiles"]:
                            if name.startswith(("msvcp", "vcruntime")):
                                copy_member(bundle, name, prefix / "runtime" / name)
                else:
                    install_cuda(archive, prefix, item, set(lock["runtimeFiles"]))
            for item in lock["sourceLibraries"]:
                build_library(archives[item["name"]], item, output, prefix, jobs, log)
            cccl = "cuda/include/cccl/cub/cub.cuh" if target.endswith("cuda13") else "cuda/include/cub/cub.cuh"
            for name in ("cuda/bin/nvcc.exe", "cuda/nvvm/libdevice/libdevice.10.bc",
                         "cuda/include/cuda_runtime.h", cccl,
                         "cuda/lib/x64/cudart_static.lib", "cuda/lib/x64/cublas.lib",
                         "cuda/lib/x64/nvrtc.lib", "cudnn/include/cudnn.h", "cudnn/lib/x64/cudnn.lib",
                         "lib/zlibstatic.lib", "lib/zip.lib"):
                if not (prefix / name).is_file():
                    raise ValueError(f"SDK component missing: {name}")
            if {p.name for p in (prefix / "runtime").iterdir()} != set(lock["runtimeFiles"]):
                raise ValueError("Runtime inventory differs from the dependency lock")
            version = subprocess.check_output([str(prefix / "cuda/bin/nvcc.exe"), "--version"],
                                              text=True, env=environment(prefix), timeout=30)
            if not re.search(r"\bV" + re.escape(lock["nvccVersion"]) + r"\b", version):
                raise ValueError("nvcc version differs from dependency lock")
            cudnn = (prefix / "cudnn/include/cudnn_version.h").read_text(encoding="utf-8")
            macros = [re.search(r"#define\s+CUDNN_" + key + r"\s+(\d+)", cudnn)
                      for key in ("MAJOR", "MINOR", "PATCHLEVEL")]
            if any(m is None for m in macros) or ".".join(m.group(1) for m in macros) != ".".join(lock["cudnnVersion"].split(".")[:3]):
                raise ValueError("cuDNN version differs from dependency lock")
            directives = subprocess.check_output(["dumpbin", "/directives", str(prefix / "lib/zlibstatic.lib")], text=True)
            if "MSVCRT" in directives.upper() or "LIBCMT" not in directives.upper():
                raise ValueError("zlib does not use the pinned static MSVC runtime")
        receipt.update(status="PASS", nvccVersionOutput=version, files=inventory(prefix),
                       dependencies=items, configuration=library_options(prefix))
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        (prefix / "sdk-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return prefix
