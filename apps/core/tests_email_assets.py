from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from apps.core.services.email_assets import attach_logo_for_src, build_email_logo_context


class EmailAssetTests(SimpleTestCase):
    def test_local_logo_uses_cid_and_attaches_inline_image(self):
        with TemporaryDirectory() as tmpdir:
            logo_dir = Path(tmpdir) / "logos"
            logo_dir.mkdir()
            logo_path = logo_dir / "egp_logo_official.png"
            logo_path.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
                b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00"
                b"\x05\xfe\x02\xfeA\xde\xfc\x83\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            with override_settings(MEDIA_ROOT=Path(tmpdir), EMAIL_LOGO_URL=""):
                context = build_email_logo_context(
                    filename="egp_logo_official.png",
                    cid="EduGrade+-logo",
                )
                message = EmailMultiAlternatives(subject="Test", body="Text", to=["user@example.test"])

                self.assertEqual(context["logo_url"], "cid:EduGrade+-logo")
                self.assertTrue(
                    attach_logo_for_src(
                        message,
                        src=context["email_logo_src"],
                        filename="egp_logo_official.png",
                        cid="EduGrade+-logo",
                    )
                )
                self.assertEqual(message.attachments[0]["Content-ID"], "<EduGrade+-logo>")

    def test_external_logo_url_does_not_attach_inline_image(self):
        context = build_email_logo_context(
            filename="egp_logo_official.png",
            cid="EduGrade+-logo",
            external_url="https://cdn.example.test/logo.png",
        )
        message = EmailMultiAlternatives(subject="Test", body="Text", to=["user@example.test"])

        self.assertEqual(context["logo_url"], "https://cdn.example.test/logo.png")
        self.assertFalse(
            attach_logo_for_src(
                message,
                src=context["email_logo_src"],
                filename="egp_logo_official.png",
                cid="EduGrade+-logo",
            )
        )
        self.assertEqual(message.attachments, [])
