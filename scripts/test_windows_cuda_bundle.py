"""Regression checks for immutable inputs, Windows dependency closure and ZIP provenance."""

import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

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


if __name__ == "__main__":
    unittest.main()
