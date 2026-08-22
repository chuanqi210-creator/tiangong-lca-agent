import hashlib
import io
import json
import stat
import struct
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path


def make_zip(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            if isinstance(entry, zipfile.ZipInfo):
                archive.writestr(entry, b"payload")
            else:
                name, payload = entry
                archive.writestr(name, payload)
    return stream.getvalue()


def patch_central_sizes(payload: bytes, *, file_size=None, compressed_size=None) -> bytes:
    changed = bytearray(payload)
    offset = changed.index(b"PK\x01\x02")
    if compressed_size is not None:
        struct.pack_into("<I", changed, offset + 20, compressed_size)
    if file_size is not None:
        struct.pack_into("<I", changed, offset + 24, file_size)
    return bytes(changed)


def patch_central_to_zip64(
    payload: bytes, *, duplicate=False, include_offset=False
) -> bytes:
    changed = bytearray(payload)
    central = changed.index(b"PK\x01\x02")
    eocd = changed.rindex(b"PK\x05\x06")
    compressed_size = struct.unpack_from("<I", changed, central + 20)[0]
    file_size = struct.unpack_from("<I", changed, central + 24)[0]
    name_length, extra_length = struct.unpack_from("<HH", changed, central + 28)
    values = struct.pack("<QQ", file_size, compressed_size)
    if include_offset:
        values += struct.pack("<Q", 0)
    zip64 = struct.pack("<HH", 0x0001, len(values)) + values
    if duplicate:
        zip64 += zip64
    insert_at = central + 46 + name_length + extra_length
    changed[insert_at:insert_at] = zip64
    struct.pack_into("<II", changed, central + 20, 0xFFFFFFFF, 0xFFFFFFFF)
    if include_offset:
        struct.pack_into("<I", changed, central + 42, 0xFFFFFFFF)
    struct.pack_into("<H", changed, central + 30, extra_length + len(zip64))
    shifted_eocd = eocd + len(zip64)
    central_size = struct.unpack_from("<I", changed, shifted_eocd + 12)[0]
    struct.pack_into("<I", changed, shifted_eocd + 12, central_size + len(zip64))
    return bytes(changed)


class ArchiveInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def inspect(self, payload, *, filename="package.zip", mime="application/zip"):
        from lca_research_service.archive_inspection import inspect_archive

        return inspect_archive(
            payload,
            input_sha256=hashlib.sha256(payload).hexdigest(),
            original_filename=filename,
            declared_media_type=mime,
            candidate_root=self.root,
        )

    def test_safe_archive_is_manifested_before_individual_content_addressed_candidates(self):
        payload = make_zip(
            [
                ("processes/p.json", b'{"@type":"Process","@id":"p-1"}'),
                ("flows/f.json", b'{"@type":"Flow","@id":"f-1"}'),
            ]
        )
        result = self.inspect(payload)

        self.assertEqual(result["contract"], "lca_research_service.archive_inspection/v1")
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["candidate_only"])
        self.assertFalse(result["scientifically_validated"])
        self.assertEqual(result["archive_manifest"]["inspection_order"], "central_directory_then_member_headers_then_content")
        self.assertEqual(result["archive_manifest"]["input_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(len(result["archive_manifest"]["members"]), 2)
        for member in result["archive_manifest"]["members"]:
            self.assertEqual(member["disposition"], "derived_candidate")
            self.assertRegex(member["sha256"], r"^[0-9a-f]{64}$")
            reference = member["candidate_ref"]
            self.assertEqual(set(reference), {"artifact_id", "sha256"})
            self.assertEqual(reference["artifact_id"], reference["sha256"])
            matches = list(self.root.rglob(reference["sha256"]))
            self.assertEqual(len(matches), 1)
            self.assertNotIn("processes", matches[0].parts)
            self.assertNotIn("flows", matches[0].parts)

    def test_large_tiangong_manifest_is_validated_from_complete_member_bytes(self):
        """Removing full-member JSON validation must reject this real package shape."""

        entries = [
            {
                "table": "sources",
                "id": str(uuid.UUID(int=index + 1)),
                "version": "01.00.000",
                "file_path": (
                    f"sources/{uuid.UUID(int=index + 1)}_01.00.000.json"
                ),
                "rule_verification": True,
            }
            for index in range(400)
        ]
        manifest = {
            "format": "tiangong-tidas-package",
            "version": 2,
            "exported_at": "2026-08-12T07:48:13Z",
            "scope": "selected_roots",
            "roots": [
                {
                    "table": "lifecyclemodels",
                    "id": "eec0e09b-6fd5-41c9-9a12-41300c9e6cfa",
                    "version": "01.01.011",
                }
            ],
            "entries": entries,
        }
        body = json.dumps(manifest, separators=(",", ":")).encode()
        self.assertGreater(len(body), 64 * 1024)

        inspected = self.inspect(make_zip([("manifest.json", body)]))
        row = inspected["archive_manifest"]["members"][0]

        self.assertEqual(inspected["status"], "ready")
        self.assertEqual(row["disposition"], "derived_candidate")
        self.assertEqual(row["detected_type"]["mime"], "application/json")
        self.assertIn(
            "tiangong_tidas_package_manifest", row["format_signatures"]
        )
        self.assertEqual(
            row["package_metadata"],
            {
                "contract": "lca_research_service.tidas_package_manifest_summary/v1",
                "format": "tiangong-tidas-package",
                "package_version": 2,
                "exported_at": "2026-08-12T07:48:13Z",
                "scope": "selected_roots",
                "roots": [
                    {
                        "table": "lifecyclemodels",
                        "id": "eec0e09b-6fd5-41c9-9a12-41300c9e6cfa",
                        "version": "01.01.011",
                    }
                ],
                "entry_count": 400,
                "entry_counts": {"sources": 400},
                "rule_verified_entry_count": 400,
            },
        )

    def test_derived_candidate_publication_rejects_preexisting_symlink(self):
        from lca_research_service.archive_inspection import ArchiveInspectionError

        content = b"source-bound member"
        digest = hashlib.sha256(content).hexdigest()
        target = self.root / "archive-members/sha256" / digest[:2] / digest
        target.parent.mkdir(parents=True)
        outside = self.root / "outside"
        outside.write_bytes(b"outside")
        target.symlink_to(outside)

        with self.assertRaisesRegex(ArchiveInspectionError, "target identity"):
            self.inspect(make_zip([("member.txt", content)]))
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_derived_candidate_publication_rejects_preexisting_directory_symlink(self):
        from lca_research_service.archive_inspection import ArchiveInspectionError

        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "archive-members").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(
            ArchiveInspectionError, "candidate directory identity"
        ):
            self.inspect(make_zip([("member.txt", b"source-bound member")]))
        self.assertEqual(list(outside.iterdir()), [])

    def test_member_name_attacks_and_special_nodes_fail_before_content_read(self):
        from lca_research_service.archive_inspection import ArchiveInspectionError

        special_types = {
            "symlink": stat.S_IFLNK | 0o777,
            "hardlink_like": stat.S_IFREG | 0o644,
            "device": stat.S_IFCHR | 0o600,
        }
        cases = [
            ("traversal", make_zip([("../escape.json", b"{}")]), "path traversal"),
            ("absolute", make_zip([("/etc/passwd", b"x")]), "absolute"),
            ("drive", make_zip([("C:/escape.json", b"{}")]), "Windows drive"),
            ("nul", make_zip([("safe.json", b"{}")]).replace(b"safe.json", b"bad\x00.json"), "NUL"),
            ("duplicate", make_zip([("a/../b.json", b"1"), ("b.json", b"2")]), "path traversal"),
            ("normalized duplicate", make_zip([("A.json", b"1"), ("a.json", b"2")]), "duplicate normalized"),
        ]
        for label, payload, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ArchiveInspectionError, message):
                self.inspect(payload)

        for label, mode in special_types.items():
            info = zipfile.ZipInfo(f"{label}.bin")
            info.create_system = 3
            info.external_attr = mode << 16
            if label == "hardlink_like":
                # A regular inode cannot encode a hardlink in ZIP.  A nonzero
                # link-count marker in the Unix extra field must still fail closed.
                info.extra = struct.pack("<HHI", 0x000D, 4, 2)
            with self.subTest(label=label), self.assertRaisesRegex(ArchiveInspectionError, "special|link|device"):
                self.inspect(make_zip([info]))

    def test_central_directory_limits_fail_without_inflating_members(self):
        from lca_research_service.archive_inspection import ArchiveInspectionError

        too_many = make_zip([(f"m/{index}.txt", b"x") for index in range(1001)])
        projected = patch_central_sizes(make_zip([("large.bin", b"x")]), file_size=500 * 1024 * 1024 + 1)
        ratio = patch_central_sizes(make_zip([("ratio.bin", b"x")]), file_size=101, compressed_size=1)
        for label, payload, message in (
            ("member count", too_many, "member count"),
            ("projection", projected, "extracted projection"),
            ("ratio", ratio, "compression ratio"),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(ArchiveInspectionError, message):
                self.inspect(payload)

    def test_nested_encrypted_corrupt_active_and_mime_drift_are_bounded(self):
        from lca_research_service.archive_inspection import ArchiveInspectionError

        nested = make_zip([("inner.zip", make_zip([("again.zip", make_zip([("x.txt", b"x")]))]))])
        encrypted = bytearray(make_zip([("secret.txt", b"secret")]))
        for signature in (b"PK\x03\x04", b"PK\x01\x02"):
            offset = encrypted.index(signature)
            flags_offset = offset + (6 if signature == b"PK\x03\x04" else 8)
            flags = struct.unpack_from("<H", encrypted, flags_offset)[0]
            struct.pack_into("<H", encrypted, flags_offset, flags | 1)
        corrupt = make_zip([("safe.txt", b"safe")])[:-12]
        executable = make_zip([("run.sh", b"#!/bin/sh\n")])
        macro = make_zip([("word/vbaProject.bin", b"macro")])
        drift = make_zip([("data.json", b"not json")])

        with self.assertRaisesRegex(ArchiveInspectionError, "nesting depth"):
            self.inspect(nested)
        self.assertEqual(self.inspect(bytes(encrypted))["status"], "needs_user")
        self.assertEqual(self.inspect(bytes(encrypted))["archive_manifest"]["members"][0]["disposition"], "rejected")
        with self.assertRaisesRegex(ArchiveInspectionError, "central directory"):
            self.inspect(corrupt)
        executable_result = self.inspect(executable)
        macro_result = self.inspect(macro)
        drift_result = self.inspect(drift)
        self.assertEqual(executable_result["status"], "partial")
        self.assertEqual(macro_result["status"], "partial")
        self.assertEqual(drift_result["status"], "partial")
        self.assertIn("executable", executable_result["archive_manifest"]["members"][0]["rejected_reasons"])
        self.assertIn("macro", macro_result["archive_manifest"]["members"][0]["rejected_reasons"])
        self.assertIn("member_mime_drift", drift_result["archive_manifest"]["members"][0]["rejected_reasons"])

    def test_rar_and_7z_are_preserved_without_decompression(self):
        rar = self.inspect(b"Rar!\x1a\x07\x01\x00", filename="data.rar", mime="application/vnd.rar")
        seven = self.inspect(b"7z\xbc\xaf'\x1c" + b"\x00" * 20, filename="data.7z", mime="application/x-7z-compressed")
        self.assertEqual(rar["status"], "needs_user")
        self.assertEqual(seven["status"], "unsupported")
        self.assertEqual(rar["archive_manifest"]["members"], [])
        self.assertEqual(seven["archive_manifest"]["members"], [])

    def test_local_header_drift_fails_before_member_content_is_consumed(self):
        from lca_research_service.archive_inspection import ArchiveInspectionError

        payload = bytearray(make_zip([("safe.txt", b"safe")]))
        local = payload.index(b"PK\x03\x04")
        payload[local + 30 : local + 38] = b"evil.txt"
        with self.assertRaisesRegex(ArchiveInspectionError, "local and central"):
            self.inspect(bytes(payload))

    def test_actual_extracted_counter_is_archive_wide_and_checked_before_write(self):
        from unittest.mock import patch
        from lca_research_service.archive_inspection import ArchiveInspectionError

        payload = make_zip([("one.txt", b"abcd"), ("two.txt", b"efgh")])
        original_infolist = zipfile.ZipFile.infolist
        original_open = zipfile.ZipFile.open

        def understated_projection(archive):
            rows = original_infolist(archive)
            for row in rows:
                row.file_size = 1
            return rows

        def observed_content(archive, info, *args, **kwargs):
            info.file_size = 4
            return original_open(archive, info, *args, **kwargs)

        with (
            patch("lca_research_service.archive_inspection.MAX_EXTRACTED_BYTES", 6),
            patch.object(zipfile.ZipFile, "infolist", understated_projection),
            patch.object(zipfile.ZipFile, "open", observed_content),
        ):
            with self.assertRaisesRegex(ArchiveInspectionError, "actual extracted bytes"):
                self.inspect(payload)

    def test_zip_comment_central_signature_is_not_scanned_as_an_entry(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("safe.txt", b"safe")
            archive.comment = b"review marker PK\x01\x02 is not a directory entry"
        payload = stream.getvalue()

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read("safe.txt"), b"safe")
        result = self.inspect(payload)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["archive_manifest"]["member_count"], 1)

    def test_valid_force_zip64_local_header_is_resolved_before_comparison(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            with archive.open("safe.txt", "w", force_zip64=True) as destination:
                destination.write(b"safe ZIP64 member")
        payload = stream.getvalue()

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read("safe.txt"), b"safe ZIP64 member")
        result = self.inspect(payload)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["archive_manifest"]["member_count"], 1)

    def test_valid_central_zip64_extra_is_resolved_in_sentinel_order(self):
        payload = patch_central_to_zip64(
            make_zip([("safe.txt", b"safe central ZIP64 member")]),
            include_offset=True,
        )
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read("safe.txt"), b"safe central ZIP64 member")
        result = self.inspect(payload)
        self.assertEqual(result["status"], "ready")

    def test_duplicate_zip64_extra_field_is_ambiguous_and_fails_closed(self):
        from lca_research_service.archive_inspection import ArchiveInspectionError

        payload = patch_central_to_zip64(
            make_zip([("safe.txt", b"safe central ZIP64 member")]),
            duplicate=True,
        )
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read("safe.txt"), b"safe central ZIP64 member")
        with self.assertRaisesRegex(ArchiveInspectionError, "ZIP64"):
            self.inspect(payload)

    def test_malformed_zip64_extra_field_fails_closed(self):
        from lca_research_service.archive_inspection import ArchiveInspectionError

        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            with archive.open("safe.txt", "w", force_zip64=True) as destination:
                destination.write(b"safe ZIP64 member")
        payload = bytearray(stream.getvalue())
        local = payload.index(b"PK\x03\x04")
        name_length = struct.unpack_from("<H", payload, local + 26)[0]
        extra = local + 30 + name_length
        self.assertEqual(payload[extra : extra + 2], b"\x01\x00")
        struct.pack_into("<H", payload, extra + 2, 8)

        with self.assertRaisesRegex(ArchiveInspectionError, "ZIP64"):
            self.inspect(bytes(payload))


if __name__ == "__main__":
    unittest.main()
