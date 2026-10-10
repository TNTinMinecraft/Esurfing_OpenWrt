import contextlib
import io
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from esurfing.__main__ import main
from esurfing.client import Transport
from esurfing.crypto import ProtocolError
from esurfing.runtime import check_runtime


class RuntimeTests(unittest.TestCase):
    def test_working_runtime(self):
        check_runtime()

    def test_missing_required_modules_explains_correct_packages(self):
        with patch("esurfing.runtime.importlib.import_module", side_effect=ModuleNotFoundError("unicodedata")):
            with self.assertRaises(ProtocolError) as caught:
                check_runtime()
        message = str(caught.exception)
        self.assertNotIn("python3-codecs", message)
        self.assertIn("python3-lzma", message)
        self.assertIn("python3-openssl", message)

    def test_missing_codec_registration_is_optional_for_ascii(self):
        with patch("esurfing.runtime.codecs.encode", side_effect=LookupError("unknown encoding: idna")):
            self.assertFalse(check_runtime()["idna"])

    def test_login_stops_before_network_and_credentials_when_dependencies_missing(self):
        with patch("esurfing.__main__.check_runtime", side_effect=ProtocolError("Install python3-lzma")), \
             patch("esurfing.__main__.network_identity") as identity, \
             patch("esurfing.__main__.Transport") as transport, \
             patch("esurfing.__main__.credentials") as credentials, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["login", "--interface", "wan"]), 1)
        self.assertIn("python3-lzma", output.getvalue())
        identity.assert_not_called()
        transport.assert_not_called()
        credentials.assert_not_called()

    def test_missing_unicodedata_in_fresh_interpreter(self):
        script = r"""
import sys, importlib.abc, socket, ssl, io
from unittest.mock import Mock, patch
class BlockUnicodeData(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'unicodedata':
            raise ModuleNotFoundError("No module named 'unicodedata'")
sys.meta_path.insert(0, BlockUnicodeData())
for name in ['unicodedata', 'encodings.idna', 'stringprep']:
    sys.modules.pop(name, None)
try:
    socket.getaddrinfo('127.0.0.1', 80, socket.AF_INET, socket.SOCK_STREAM)
except LookupError:
    pass
else:
    raise AssertionError('Expected original CPython path to reproduce the IDNA failure')
from esurfing.client import Transport
from esurfing.runtime import check_runtime
assert check_runtime()['idna'] is False
fake = Mock()
fake.makefile.return_value = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK')
with patch('esurfing.network.socket.socket', return_value=fake):
    response = Transport('127.0.0.1').request('GET', 'http://127.0.0.1/', {})
assert response.status == 200 and response.body == b'OK'
context = ssl.create_default_context()
assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
with context.wrap_socket(socket.socket(), server_hostname=b'portal.example', do_handshake_on_connect=False) as wrapped:
    assert wrapped.server_hostname == 'portal.example'
print('PASS without unicodedata')
"""
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS without unicodedata", result.stdout)

    def test_transport_lookup_error_is_actionable_and_connection_closed(self):
        with patch("esurfing.client.http.client.HTTPConnection") as constructor:
            connection = constructor.return_value
            connection.request.side_effect = LookupError("unknown encoding: idna")
            with self.assertRaisesRegex(ProtocolError, "python3-codecs"):
                Transport("192.0.2.2").request("GET", "http://preportal.go189.cn/detect.html", {})
            connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
