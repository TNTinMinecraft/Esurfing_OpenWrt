import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from esurfing.crypto import Algorithm, ProtocolError, UnsupportedAlgorithm, PROBE, ZERO_ID, checksum
from esurfing.des import des, schedule
from esurfing.client import (Client, Identity, Response, ServerError, discover, parse_xml,
                             local_time, request_xml, checked_url)

IDENTITY = Identity("192.0.2.2", "02-00-00-00-00-02", "192.0.2.1",
                    "00000000-0000-4000-8000-000000000001", "test-host")
BASE = "http://192.0.2.1/"
ROOT = Path(__file__).resolve().parents[1]


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body=None):
        self.calls.append((method, url, headers, body))
        if not self.responses:
            raise AssertionError("Unexpected HTTP call")
        return self.responses.pop(0)


def reply(algorithm, xml, code="0"):
    return Response(200, {"error-code": code}, algorithm.encrypt(xml.encode()))


class CryptoTests(unittest.TestCase):
    def test_standard_des_known_answer(self):
        key = schedule(bytes.fromhex("133457799BBCDFF1"))
        ciphertext = des(bytes.fromhex("0123456789ABCDEF"), key)
        self.assertEqual(ciphertext.hex(), "85e813540f0ab405")
        self.assertEqual(des(ciphertext, key[::-1]).hex(), "0123456789abcdef")

    def test_padding_boundaries_and_unicode(self):
        algorithm = Algorithm("test", bytes(range(48)))
        for value in [b"", b"a", b"12345678", b"123456789", "测试认证".encode()]:
            encrypted = algorithm.encrypt(value)
            self.assertEqual(algorithm.decrypt(encrypted), value)
            self.assertEqual(len(encrypted), (len(value)//8+1)*16)
        with self.assertRaises(ProtocolError):
            algorithm.encrypt(b"a\0b")
        for value in [b"", b"not hex", b"AA", b"00"*8]:
            with self.assertRaises(ProtocolError):
                algorithm.decrypt(value)

    def test_captured_algorithm_image(self):
        from esurfing.crypto import unpack_package
        package = ROOT/"analysis/algorithm.bin"
        if not package.exists():
            self.skipTest("local capture fixture not present")
        identifier, image = unpack_package(package.read_bytes())
        self.assertEqual(identifier, "a57d523b-32fb-11f1-a14c-fa163e7b1075")
        self.assertEqual(hashlib.sha256(image).hexdigest(),
                         "2e9773bc4bf168ed9a9c8846ea1c740bb19ac35adec4d407a78303becd402be2")
        Algorithm.from_package(package.read_bytes())
        changed = bytearray(image)
        changed[1024] ^= 1
        with patch("esurfing.crypto.unpack_package", return_value=(identifier, bytes(changed))):
            with self.assertRaises(UnsupportedAlgorithm):
                Algorithm.from_package(b"unused")

    def test_bad_envelopes(self):
        from esurfing.crypto import unpack_package
        for value in [b"", b"001", b"001\xff" + bytes(110), b"002" + bytes(200)]:
            with self.assertRaises(ProtocolError):
                unpack_package(value)

    def test_captured_probe_checksum(self):
        self.assertEqual(checksum(PROBE), "c4ba71a6fcb815e45ccc111ad118a48c")


class DiscoveryTests(unittest.TestCase):
    def test_http_header_and_meta_redirects(self):
        page = (b'<!--//config.campus.js.chinatelecom.com<config>'
                b'<ticket-url><![CDATA[/ticket.cgi?x=1&y=2]]></ticket-url>'
                b'<auth-url>/auth.cgi</auth-url></config>//config.campus.js.chinatelecom.com-->')
        transport = ScriptedTransport([
            Response(200, {"refresh": "0;URL=/first"}, b""),
            Response(302, {"location": "/second"}, b""),
            Response(200, {}, b'<meta content="0; url=/index" http-equiv="Refresh">'),
            Response(200, {}, page),
        ])
        self.assertEqual(discover(transport, BASE), (BASE+"ticket.cgi?x=1&y=2", BASE+"auth.cgi"))
        self.assertEqual(len(transport.calls), 4)
        self.assertTrue(all(call[0] == "GET" and call[3] is None for call in transport.calls))

    def test_discovery_failure_and_loop(self):
        for responses in [[Response(200, {}, b"already online")], [Response(302, {"location": BASE}, b"")]]:
            with self.assertRaises(ProtocolError):
                discover(ScriptedTransport(responses), BASE)

    def test_bad_urls(self):
        for value in ["file:///tmp/test", "http://user:pass@example.com", "http://host:bad/", "http://host/\r\nX:x"]:
            with self.assertRaises(ProtocolError):
                checked_url(value)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.algorithm = Algorithm("00000000-0000-4000-8000-000000000002", bytes(range(48)))

    def make_client(self, transport):
        client = Client(transport, IDENTITY, BASE+"ticket.cgi", BASE+"auth.cgi", clock=lambda: "00000002d")
        client.algorithm = self.algorithm
        return client

    def test_complete_session(self):
        responses = [
            reply(self.algorithm, "<response><ticket>fake-ticket</ticket><expire>3600</expire></response>"),
            reply(self.algorithm, "<response><keep-url>/keep.cgi</keep-url><term-url>/term.cgi</term-url><keep-retry>5</keep-retry></response>"),
            reply(self.algorithm, "<response><interval>300</interval><level>0</level></response>"),
            Response(200, {"error-code": "0"}, b""),
        ]
        transport = ScriptedTransport(responses)
        client = self.make_client(transport)
        self.assertEqual(client.login("test-user", "test-password&<>"), 3600)
        self.assertTrue(client.active)
        self.assertEqual(client.keep(), 300)
        client.logout()
        self.assertFalse(client.active)
        self.assertIsNone(client.ticket)
        self.assertEqual([call[1] for call in transport.calls], [BASE+x for x in ["ticket.cgi", "auth.cgi", "keep.cgi", "term.cgi"]])
        for method, url, headers, body in transport.calls:
            self.assertEqual(headers["CDC-Checksum"], checksum(body))
            self.assertEqual(headers["Client-ID"], IDENTITY.client_id)
        auth = parse_xml(self.algorithm.decrypt(transport.calls[1][3]), "request")
        self.assertEqual(auth.findtext("passwd"), "test-password&<>")
        keep = parse_xml(self.algorithm.decrypt(transport.calls[2][3]), "request")
        self.assertIsNone(keep.find("excheck-args"))

    def test_negotiation_and_key_update(self):
        updated = Algorithm("00000000-0000-4000-8000-000000000003", bytes(range(48, 96)),
                            "triple-modified-tea")
        transport = ScriptedTransport([
            Response(200, {"error-code": "200"}, b"package1"),
            Response(200, {"error-code": "200"}, b"package2"),
            reply(updated, "<response><ticket>t</ticket><expire>3600</expire></response>"),
            reply(updated, "<response><keep-url>/keep.cgi</keep-url><term-url>/term.cgi</term-url></response>"),
        ])
        client = self.make_client(transport)
        client.algorithm = None
        with patch.object(Algorithm, "from_package", side_effect=[self.algorithm, updated]):
            client.login("u", "p")
        self.assertEqual(transport.calls[0][2]["Algo-ID"], ZERO_ID)
        self.assertEqual(transport.calls[0][3], PROBE)
        self.assertEqual(transport.calls[1][2]["Algo-ID"], self.algorithm.identifier)
        self.assertEqual(transport.calls[2][2]["Algo-ID"], updated.identifier)
        self.assertEqual(self.algorithm.decrypt(transport.calls[1][3]), updated.decrypt(transport.calls[2][3]))

    def test_failed_auth_does_not_become_active_or_retry_credentials(self):
        transport = ScriptedTransport([
            reply(self.algorithm, "<response><ticket>t</ticket><expire>3600</expire></response>"),
            Response(200, {"error-code": "123"}, b"secret error content"),
        ])
        client = self.make_client(transport)
        with self.assertRaises(ServerError) as caught:
            client.login("u", "p")
        self.assertNotIn("secret", str(caught.exception))
        self.assertFalse(client.active)
        self.assertEqual(len(transport.calls), 2)

    def test_unrecognized_keep_cannot_silently_succeed(self):
        for xml in ["<response><interval>0</interval><level>0</level></response>",
                    "<response><interval>300</interval><level>1</level></response>",
                    "<response><interval>300</interval><level>0</level><excheck><cmd>unknown</cmd></excheck></response>",
                    "<response></response>"]:
            client = self.make_client(ScriptedTransport([reply(self.algorithm, xml)]))
            client.active = True
            client.ticket = "t"
            client.keep_url = BASE+"keep.cgi"
            with self.assertRaises(ProtocolError):
                client.keep()

    def test_xml_entities_not_allowed(self):
        with self.assertRaises(ProtocolError):
            parse_xml(b'<!DOCTYPE response [<!ENTITY test "abc">]><response>&test;</response>', "response")
        with self.assertRaises(ProtocolError):
            request_xml([("value", "bad\x01")])

    def test_local_time_encoding(self):
        self.assertEqual(local_time(180 << 34), "00000002d")


if __name__ == "__main__":
    unittest.main()
