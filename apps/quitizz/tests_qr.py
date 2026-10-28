"""Opaque QR canvas/quiet-zone and unchanged real-session joining contracts."""
from urllib.parse import urlsplit
from unittest.mock import patch
import xml.etree.ElementTree as ET

from django.test import TestCase, Client
from reportlab.graphics.barcode.qr import QrCodeWidget

from . import gameplay as game, tests_phase4 as fixtures


class ProjectorQRTests(TestCase):
    setUpTestData = classmethod(fixtures.Phase4Tests.setUpTestData.__func__)
    new_user = classmethod(fixtures.Phase4Tests.new_user.__func__)
    grant = classmethod(fixtures.Phase4Tests.grant.__func__)
    for _helper in ["enable", "args", "quiz", "question", "content", "mutate", "url", "command", "lobby", "player", "answer"]:
        locals()[_helper] = getattr(fixtures.Phase4Tests, _helper)

    def setUp(self):
        fixtures.Phase4Tests.setUp(self)
        self.lobby()

    def qr_response(self):
        widgets = []
        def create(*args, **kwargs):
            widget = QrCodeWidget(*args, **kwargs); widgets.append(widget); return widget
        with patch("apps.quitizz.views.QrCodeWidget", side_effect=create):
            response = self.client.get(self.url("host_qr"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn("no-store", response["Cache-Control"])
        return response, widgets[0]

    def test_real_payload_svg_has_opaque_white_canvas_crisp_black_modules_and_four_module_border(self):
        response, widget = self.qr_response()
        root = ET.fromstring(response.content)
        self.assertEqual(root.attrib["viewBox"], "0 0 440 440")
        self.assertEqual(root.attrib["shape-rendering"], "crispEdges")
        self.assertEqual(widget.barBorder, 4)
        url = urlsplit(widget.value)
        self.assertEqual(url.path, self.url("play")); self.assertEqual(url.netloc, "testserver")
        game.verify_signed(self.session, url.fragment, salt=game.CAPABILITY_SALT, max_age=game.HOURS * 3600)
        ns = {"s": "http://www.w3.org/2000/svg"}
        rectangles = root.findall(".//s:rect", ns)
        white = [rect for rect in rectangles if "fill: rgb(100%,100%,100%)" in rect.get("style", "")]
        self.assertEqual(len(white), 1)
        self.assertEqual([white[0].get(key) for key in ["x", "y", "width", "height"]], ["0", "0", "440", "440"])
        black = [rect for rect in rectangles if "fill: rgb(0%,0%,0%)" in rect.get("style", "")]
        self.assertTrue(black)
        count = widget.qr.getModuleCount(); unit = 440 / (count + 8)
        matrix = [[False] * count for _ in range(count)]
        for rect in black:
            x, y, width, height = [float(rect.get(key)) for key in ["x", "y", "width", "height"]]
            self.assertGreaterEqual(min(x, y), 4 * unit - .000001)
            self.assertLessEqual(max(x + width, y + height), 440 - 4 * unit + .000001)
            self.assertAlmostEqual(height, unit)
            row = round(count + 4 - (y + height) / unit)
            column = round(x / unit - 4)
            for offset in range(round(width / unit)): matrix[row][column + offset] = True
        self.assertEqual(matrix, [[bool(value) for value in row] for row in widget.qr.modules])

    def test_join_generation_rotation_and_closed_joining_are_preserved(self):
        _, first = self.qr_response()
        token = urlsplit(first.value).fragment
        grant = game.exchange(self.session.public_id, token)
        participant, credential = game.join(self.session.public_id, grant, "QR guest")
        self.assertEqual(game.state(self.session.public_id, credential)["nickname"], participant.nickname)
        self.command("close_joining")
        self.assertNotEqual(self.client.get(self.url("host_qr")).status_code, 200)
        self.command("open_joining")
        _, second = self.qr_response()
        self.assertNotEqual(second.value, first.value)
        with self.assertRaises(game.Unavailable): game.exchange(self.session.public_id, token)
        self.assertTrue(game.exchange(self.session.public_id, urlsplit(second.value).fragment))

    def test_qr_stays_owned_scoped_feature_gated_and_direct_deny_protected(self):
        url = self.url("host_qr")
        self.assertIn(Client().get(url).status_code, [302, 403])
        self.grant(self.other_user, "quitizz.host")
        other = Client(); other.force_login(self.other_user)
        self.assertEqual(other.get(url).status_code, 404)
        self.enable(False); self.assertEqual(self.client.get(url).status_code, 403)
        self.enable(True); self.grant(self.user, "quitizz.host", grant_type="DENY")
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_lobby_counts_65_synthetic_players_without_changing_session_or_qr_identity(self):
        version = self.session.state_version
        for index in range(65): self.player(f"QR guest {index}")
        for endpoint in ["host_state", "projector_state"]:
            response = self.client.get(self.url(endpoint)); self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["participant_count"], 65)
            self.assertEqual(response.json()["version"], version)
        _, qr = self.qr_response()
        self.assertIn(str(self.session.public_id), qr.value)
