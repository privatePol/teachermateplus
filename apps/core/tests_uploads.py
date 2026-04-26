from __future__ import annotations

from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from apps.core.services.uploads import UploadValidationService
from apps.core.upload_paths import correction_attachment_upload_path, import_source_upload_path


class UploadValidationServiceTests(SimpleTestCase):
    def _png_file(self, name: str = "evidence.png") -> SimpleUploadedFile:
        buffer = BytesIO()
        Image.new("RGB", (4, 4), color="white").save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_correction_attachment_allows_valid_pdf(self):
        uploaded = SimpleUploadedFile("petition.pdf", b"%PDF-1.7\ncontent", content_type="application/pdf")

        result = UploadValidationService.validate_correction_attachment(uploaded)

        self.assertEqual(result.original_filename, "petition.pdf")
        self.assertEqual(result.content_type, "application/pdf")
        self.assertGreater(result.file_size_bytes, 0)

    def test_correction_attachment_allows_valid_image(self):
        uploaded = self._png_file()

        result = UploadValidationService.validate_correction_attachment(uploaded)

        self.assertEqual(result.original_filename, "evidence.png")
        self.assertEqual(result.content_type, "image/png")

    def test_correction_attachment_rejects_disallowed_extension(self):
        uploaded = SimpleUploadedFile("script.exe", b"MZ", content_type="application/octet-stream")

        with self.assertRaises(ValidationError):
            UploadValidationService.validate_correction_attachment(uploaded)

    def test_import_csv_rejects_binary_content(self):
        uploaded = SimpleUploadedFile("students.csv", b"student_no,name\x00bad", content_type="text/csv")

        with self.assertRaises(ValidationError):
            UploadValidationService.validate_import_csv(uploaded)

    def test_upload_paths_randomize_stored_names(self):
        correction_path = correction_attachment_upload_path(None, "my evidence.pdf")
        import_path = import_source_upload_path(None, "students.csv")

        self.assertTrue(correction_path.startswith("correction_attachments/"))
        self.assertTrue(correction_path.endswith(".pdf"))
        self.assertNotIn("my evidence", correction_path)
        self.assertTrue(import_path.startswith("imports/"))
        self.assertTrue(import_path.endswith(".csv"))
        self.assertNotIn("students", import_path)
