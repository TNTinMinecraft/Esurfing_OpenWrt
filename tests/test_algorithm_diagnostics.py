import contextlib
import io
import lzma
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from esurfing.__main__ import main
from esurfing.client import Response
from esurfing.crypto import PROBE, ZERO_ID, ProtocolError, decompress_image
from esurfing.diagnostics import save_algorithm


class MarkerlessLzmaTests(unittest.TestCase):
    # Generated with liblzma's LZMA1EXT encoder, ext_flags=0 (no end marker),
    # lc=3, lp=0, pb=2, dictionary=4096. No liblzma ctypes dependency at runtime.
    PLAIN = bytes.fromhex('bebef5e4a8fcbb70043ab24cc33a2814c838ec4964e21889093631')
    COMPRESSED = bytes.fromhex('005f2f9aae99bb842e4234af1ee1659978bf4eb6d6c692d788a683f42fcf91f49b2400')
    PROPS = bytes.fromhex('5d00100000')

    def test_markerless_flush_bytes_can_produce_extra_output(self):
        old = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{
            'id': lzma.FILTER_LZMA1, 'dict_size': 4096, 'lc': 3, 'lp': 0, 'pb': 2}])
        result = old.decompress(self.COMPRESSED, max_length=len(self.PLAIN)+1)
        self.assertEqual(len(result), len(self.PLAIN)+1)
        self.assertFalse(old.eof)
        self.assertEqual(decompress_image(self.COMPRESSED, self.PROPS, len(self.PLAIN)), self.PLAIN)

    def test_incomplete_stream_still_rejected_with_sizes(self):
        with self.assertRaisesRegex(ProtocolError, r'expected=27, actual=\d+'):
            decompress_image(self.COMPRESSED[:10], self.PROPS, len(self.PLAIN))


class AlgorithmDiagnosticTests(unittest.TestCase):
    def test_exclusive_private_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'algorithm.bin'
            info = save_algorithm(path, b'initial algorithm response')
            self.assertEqual(info['package_bytes'], len(path.read_bytes()))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(ProtocolError):
                save_algorithm(path, b'overwrite')
            self.assertEqual(path.read_bytes(), b'initial algorithm response')

    def test_fetch_saves_even_unsupported_response_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)/'algorithm.bin'
            with patch('esurfing.__main__.network_identity', return_value=('192.0.2.2','02-00-00-00-00-01','192.0.2.1')), \
                 patch('esurfing.__main__.stable_client_id', return_value='00000000-0000-4000-8000-000000000001'), \
                 patch('esurfing.__main__.Transport') as transport, \
                 patch('esurfing.__main__.credentials') as credentials, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                transport.return_value.request.return_value = Response(200, {'error-code':'200'}, b'unknown-algorithm')
                args = ['fetch-algorithm','--interface','wan','--output',str(destination),
                        '--ticket-url','http://192.0.2.1/ticket.cgi','--auth-url','http://192.0.2.1/auth.cgi']
                self.assertEqual(main(args), 1)
                self.assertEqual(destination.read_bytes(), b'unknown-algorithm')
                self.assertIn('sha256', output.getvalue())
                self.assertIn('Unsupported AutoZSM', output.getvalue())
                request = transport.return_value.request.call_args.args
                self.assertEqual(request[0], 'POST')
                self.assertEqual(request[2]['Algo-ID'], ZERO_ID)
                self.assertEqual(request[3], PROBE)
                credentials.assert_not_called()
                transport.reset_mock()
                self.assertEqual(main(args), 1)
                transport.assert_not_called()

    def test_failed_negotiation_does_not_prompt_for_password(self):
        with patch('esurfing.__main__.check_runtime'), \
             patch('esurfing.__main__.network_identity', return_value=('192.0.2.2','02-00-00-00-00-01','192.0.2.1')), \
             patch('esurfing.__main__.stable_client_id', return_value='00000000-0000-4000-8000-000000000001'), \
             patch('esurfing.__main__.Transport'), \
             patch('esurfing.__main__.Client') as client, \
             patch('esurfing.__main__.credentials') as credentials, \
             contextlib.redirect_stdout(io.StringIO()):
            client.return_value.negotiate.side_effect = ProtocolError('Unknown algorithm')
            self.assertEqual(main(['login','--interface','wan',
                                  '--ticket-url','http://192.0.2.1/ticket.cgi',
                                  '--auth-url','http://192.0.2.1/auth.cgi']), 1)
            credentials.assert_not_called()


if __name__ == '__main__':
    unittest.main()
