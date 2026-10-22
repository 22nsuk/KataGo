"""Regression checks for immutable inputs, Windows dependency closure and ZIP provenance."""

import copy
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import build_windows_cuda_bundle as bundle
import windows_cuda_sdk as sdk


class DependencyTests(unittest.TestCase):
    def test_both_profiles_pin_static_zlib_and_complete_compilers(self):
        for target in sdk.PROFILES:
            lock = sdk.load_lock(target)
            libraries = {i["name"]: i for i in lock["sourceLibraries"]}
            self.assertEqual("static", libraries["zlib"]["linkage"])
            self.assertEqual("MultiThreaded", libraries["zlib"]["msvcRuntime"])
            self.assertEqual("1.3.1", libraries["zlib"]["version"])
            names = {i["name"] for i in lock["dependencies"]}
            self.assertTrue({"cuda_nvcc", "cuda_cccl", "cuda_cudart", "cuda_nvrtc", "cudnn"} <= names)
            if target.endswith("cuda13"):
                self.assertTrue({"cuda_crt", "libnvvm"} <= names)

    def test_corrupted_dependency_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dependency.zip"
            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                sdk.verify_input(path, {"name": "test", "sizeBytes": 8, "sha256": "0" * 64})

    def test_cached_download_is_revalidated_without_network(self):
        data = b"cached locked archive"
        item = {"name": "fixture", "url": "https://example.invalid/archive.zip",
                "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            archive = cache / (item["sha256"] + "-archive.zip")
            archive.write_bytes(data)
            with patch.object(sdk.subprocess, "run") as network:
                self.assertEqual(sdk.download(item, cache, io.StringIO()), archive)
                network.assert_not_called()

    def test_corrupt_cache_hit_does_not_bypass_integrity(self):
        data = b"cached locked archive"
        item = {"name": "fixture", "url": "https://example.invalid/archive.zip",
                "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for changed in (b"x" * len(data), data + b"extra"):
            with self.subTest(payload=changed), tempfile.TemporaryDirectory() as temp:
                cache = Path(temp)
                (cache / (item["sha256"] + "-archive.zip")).write_bytes(changed)
                with patch.object(sdk.subprocess, "run") as network:
                    with self.assertRaisesRegex(ValueError, "size or SHA-256 mismatch"):
                        sdk.download(item, cache, io.StringIO())
                    network.assert_not_called()

    def test_sdk_receipt_rejects_changed_or_extra_files(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            path = prefix / "library.lib"
            path.write_bytes(b"locked bytes")
            receipt = dict(status="PASS", target="windows-nvidia",
                           lockSha256=sdk.digest(sdk.PROFILES["windows-nvidia"]), files=sdk.inventory(prefix))
            (prefix / "sdk-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            self.assertEqual(receipt, sdk.verify_sdk(prefix, "windows-nvidia"))
            (prefix / "extra.dll").write_bytes(b"unexpected bytes")
            with self.assertRaisesRegex(ValueError, "changed"):
                sdk.verify_sdk(prefix, "windows-nvidia")

    def test_source_revision_must_be_exact_and_clean(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-c", "user.name=Bundle Test", "-c", "user.email=bundle@example.invalid",
                            "commit", "-q", "--allow-empty", "-m", "fixture"], cwd=source, check=True)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
            bundle.source_check(source, sha)
            with self.assertRaisesRegex(ValueError, "requested SHA"):
                bundle.source_check(source, "0" * 40)
            (source / "untracked.cpp").write_text("changed source", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "clean"):
                bundle.source_check(source, sha)

    def test_archive_traversal_and_case_collisions_are_rejected(self):
        for names in (("root/../evil",), ("root/bin/A.dll", "root/bin/a.dll")):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for name in names:
                    archive.writestr(name, b"test")
            buffer.seek(0)
            with zipfile.ZipFile(buffer) as archive, self.assertRaises(ValueError):
                sdk.zip_entries(archive)

    def test_both_official_runtime_layouts_are_installed_without_compiler_dlls(self):
        for member in ("bin/runtime.dll", "bin/x64/runtime.dll"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = root / "runtime.zip"
                with zipfile.ZipFile(archive, "w") as zipped:
                    zipped.writestr("root/LICENSE", "vendor notice")
                    zipped.writestr("root/" + member, "runtime bytes")
                    zipped.writestr("root/nvvm/bin/compiler.dll", "compiler bytes")
                prefix = root / "prefix"
                sdk.install_cuda(archive, prefix, {"name": "component", "destination": "cuda"}, {"runtime.dll"})
                self.assertEqual(b"runtime bytes", (prefix / "runtime/runtime.dll").read_bytes())
                self.assertFalse((prefix / "runtime/compiler.dll").exists())

    def test_dependency_closure_and_static_linkage(self):
        headers = "8664 machine (x64)"
        valid = "    KERNEL32.dll\n    cublas64_13.dll\n    cudnn64_9.dll\n"
        runtime = {"cublas64_13.dll", "cudnn64_9.dll"}
        self.assertEqual(3, len(bundle.inspect_pe(headers, valid, runtime, executable=True)["needed"]))
        with self.assertRaisesRegex(ValueError, "Unapproved PE dependency"):
            bundle.inspect_pe(headers, valid, {"cudnn64_9.dll"})
        with self.assertRaises(ValueError):
            bundle.inspect_pe(headers, valid + "    z.dll\n", runtime, executable=True)
        with self.assertRaisesRegex(ValueError, "statically link"):
            bundle.inspect_pe(headers, valid + "    vcruntime140.dll\n", runtime | {"vcruntime140.dll"}, executable=True)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = self.root / "files"
        self.files.mkdir()
        for name in ("katago.exe", "default_gtp.cfg", "analysis_example.cfg", "licenses/KataGo/LICENSE"):
            path = self.files / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name, encoding="utf-8")
        self.sha = "a" * 40
        self.target = "windows-nvidia-cuda13"
        self.receipt = dict(sourceCommit=self.sha, sourceRepository=bundle.SOURCE_REPOSITORY,
                            target=self.target, backend="CUDA", origin="project-source-build",
                            buildStatus="PASS", packagingStatus="PASS", dependencyAuditStatus="PASS",
                            executable=sdk.record(self.files / "katago.exe", self.files),
                            files=sdk.inventory(self.files))

    def tearDown(self):
        self.temp.cleanup()

    def write(self, receipt=None):
        path = self.root / "bundle.zip"
        bundle.seal_archive(path, self.files, receipt or self.receipt)
        return path

    def test_valid_exact_inventory_is_deterministic(self):
        first = self.write()
        second = self.root / "second.zip"
        bundle.seal_archive(second, self.files, self.receipt)
        self.assertEqual(sdk.digest(first), sdk.digest(second))
        self.assertEqual(self.receipt, bundle.verify_archive(first, self.target, self.sha))

    def test_wrong_source_or_target_is_rejected(self):
        path = self.write()
        for target, sha in (("windows-nvidia", self.sha), (self.target, "b" * 40)):
            with self.assertRaises(ValueError):
                bundle.verify_archive(path, target, sha)

    def test_modified_file_and_missing_config_are_rejected(self):
        (self.files / "katago.exe").write_bytes(b"different bytes")
        with self.assertRaisesRegex(ValueError, "modified"):
            bundle.verify_archive(self.write(), self.target, self.sha)
        (self.root / "bundle.zip").unlink()
        receipt = copy.deepcopy(self.receipt)
        receipt["files"] = [r for r in receipt["files"] if r["file"] != "default_gtp.cfg"]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            bundle.verify_archive(self.write(receipt), self.target, self.sha)


class BuildReceiptTests(unittest.TestCase):
    """Exercise real packaging and receipts with the native toolchain stubbed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.prefix = self.root / "sdk"
        self.sha = "a" * 40
        self.target = "windows-nvidia-cuda13"
        lock = sdk.load_lock(self.target)
        for name in ("LICENSE", "cpp/configs/gtp_example.cfg", "cpp/configs/analysis_example.cfg"):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        for name in ["share/licenses/LICENSE", *("runtime/" + name for name in lock["runtimeFiles"])]:
            path = self.prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        receipt = dict(status="PASS", target=self.target, lockSha256=sdk.digest(sdk.PROFILES[self.target]),
                       files=sdk.inventory(self.prefix), msvcToolset="14.44", windowsSdk="fixture",
                       nvccVersionOutput="fixture")
        (self.prefix / "sdk-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    def run_tool(self, command, **kwargs):
        if command[0] == "cmake":
            if command[1] == "--build":
                build = Path(command[2])
                compiler = build / "CMakeFiles/3.31.6/CMakeCXXCompiler.cmake"
                compiler.parent.mkdir(parents=True)
                compiler.write_text('set(CMAKE_CXX_COMPILER_ID "MSVC")\n'
                                    'set(CMAKE_CXX_COMPILER_VERSION "19.44")\n'
                                    'set(CMAKE_CXX_COMPILER "cl.exe")\n', encoding="utf-8")
                (build / "katago.exe").write_bytes(b"fixture executable")
            return subprocess.CompletedProcess(command, 0)
        self.assertEqual(Path(command[0]).name, "katago.exe")
        self.assertIn(command[1], ("version", "runtests", "runoutputtests"))
        return subprocess.CompletedProcess(command, 0, stdout=f"Git revision: {self.sha}\nUsing CUDA backend\n")

    def tool_output(self, command, **kwargs):
        if command[:2] == ["dumpbin", "/headers"]:
            return "8664 machine (x64)\n"
        if command[:2] == ["dumpbin", "/dependents"]:
            return "    KERNEL32.dll\n"
        self.assertIn(command, (["cmake", "--version"], ["ninja", "--version"]))
        return "fixture version\n"

    def run_build(self, output):
        with (patch.object(bundle, "source_check"), patch.object(sdk, "host_check"),
              patch.dict(os.environ, {"SYSTEMROOT": str(self.root / "Windows")}),
              patch.object(bundle.subprocess, "run", side_effect=self.run_tool),
              patch.object(bundle.subprocess, "check_output", side_effect=self.tool_output)):
            return bundle.build(self.source, output, self.root / "cache", self.target, self.sha, 2, self.prefix)

    def test_success_agrees_across_receipts_archive_and_summary(self):
        output = self.root / "success"
        summary = self.run_build(output)
        receipt = json.loads((output / "source-package.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["packagingStatus"], "PASS")
        self.assertNotIn("error", receipt)
        self.assertEqual(receipt["hardwareAcceptanceStatus"], "PENDING_HARDWARE")
        archive = output / summary["archive"]["file"]
        self.assertEqual(bundle.verify_archive(archive, self.target, self.sha), receipt)
        metadata = output / "release/source-release.json"
        self.assertEqual(json.loads(metadata.read_text(encoding="utf-8")), receipt)
        self.assertEqual(summary["archive"], sdk.record(archive, output))
        self.assertEqual(summary["sourceMetadata"], sdk.record(metadata, metadata.parent))
        self.assertEqual(json.loads((output / "artifact-summary.json").read_text(encoding="utf-8")), summary)

    def test_packaging_failures_never_leave_a_pass_receipt(self):
        for stage in ("copy_licenses", "seal_archive", "verify_archive", "source-release.json", "artifact-summary.json"):
            with self.subTest(stage=stage), ExitStack() as failure:
                output = self.root / stage
                message = "injected " + stage + " failure"
                if stage.endswith(".json"):
                    write_text = Path.write_text

                    def fail_write(path, *args, **kwargs):
                        if path.name == stage:
                            raise OSError(message)
                        return write_text(path, *args, **kwargs)

                    failure.enter_context(patch.object(Path, "write_text", fail_write))
                else:
                    failure.enter_context(patch.object(bundle, stage, side_effect=OSError(message)))
                with self.assertRaisesRegex(OSError, message):
                    self.run_build(output)
                receipt = json.loads((output / "source-package.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["packagingStatus"], "FAIL")
                self.assertEqual(receipt["buildStatus"], "PASS")
                self.assertEqual(receipt["dependencyAuditStatus"], "PASS")
                self.assertEqual(receipt["error"], message)
                self.assertFalse((output / "artifact-summary.json").exists())

    def test_build_failure_leaves_packaging_not_run(self):
        output = self.root / "build-failure"
        with patch.object(self, "run_tool", side_effect=OSError("injected build failure")):
            with self.assertRaisesRegex(OSError, "injected build failure"):
                self.run_build(output)
        receipt = json.loads((output / "source-package.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["buildStatus"], "FAIL")
        self.assertEqual(receipt["packagingStatus"], "NOT_RUN")
        self.assertEqual(receipt["dependencyAuditStatus"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
