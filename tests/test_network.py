import contextlib
import io
import json
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import Mock, call, patch

from esurfing.__main__ import main
from esurfing.client import Transport, ASCIIHTTPSConnection
from esurfing.crypto import ProtocolError
from esurfing.network import macos_connection, network_identity, system_output, ipv4_connection

IFCONFIG = """en0: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    ether 02:ab:cd:12:34:56
    inet6 fe80::1234%en0 prefixlen 64 secured scopeid 0xc
    inet 192.0.2.10 netmask 0xffffff00 broadcast 192.0.2.255
    status: active
"""
ROUTE = """   route to: default
 destination: default
     gateway: 192.0.2.1
   interface: en0
       flags: <UP,GATEWAY,DONE,STATIC>
"""


class NetworkIdentityTests(unittest.TestCase):
    @patch("esurfing.network.sys.platform", "darwin")
    def test_macos_reads_ipv4_mac_and_interface_scoped_gateway(self):
        with patch("esurfing.network.system_output", side_effect=[IFCONFIG, ROUTE]) as run:
            self.assertEqual(network_identity("en0"), ("192.0.2.10", "02-AB-CD-12-34-56", "192.0.2.1"))
        self.assertEqual(run.call_args_list, [call(["/sbin/ifconfig", "en0"]),
                         call(["/sbin/route", "-n", "get", "-inet", "-ifscope", "en0", "default"])])

    @patch("esurfing.network.sys.platform", "darwin")
    def test_other_interface_gateway_is_rejected(self):
        with patch("esurfing.network.system_output", side_effect=[IFCONFIG, ROUTE.replace("en0", "utun0")]):
            with self.assertRaisesRegex(ProtocolError, "different interface"):
                network_identity("en0")

    @patch("esurfing.network.sys.platform", "darwin")
    def test_ipv6_only_and_missing_ether_are_actionable(self):
        for output, message in [("en0: flags=UP\n inet6 fe80::1\n", "No IPv4"),
                                ("utun0: flags=UP\n inet 192.0.2.10 netmask 0xffffffff\n", "No Ethernet")]:
            with patch("esurfing.network.system_output", return_value=output):
                with self.assertRaisesRegex(ProtocolError, message):
                    network_identity("en0")

    @patch("esurfing.network.sys.platform", "darwin")
    def test_override_gateway_skips_route_query(self):
        with patch("esurfing.network.system_output", return_value=IFCONFIG) as run:
            result = network_identity("en0", gateway="192.0.2.254")
        self.assertEqual(result[2], "192.0.2.254")
        run.assert_called_once_with(["/sbin/ifconfig", "en0"])

    def test_full_overrides_work_without_os_commands_and_validate_values(self):
        with patch("esurfing.network.system_output") as run, patch("esurfing.network.sys.platform", "darwin"):
            result = network_identity("en5", "192.0.2.2", "02:00:00:00:00:01", "192.0.2.1")
            self.assertEqual(result[1], "02-00-00-00-00-01")
            run.assert_not_called()
            for ip, mac, gateway in [("invalid", result[1], result[2]),
                                     (result[0], "invalid", result[2]),
                                     (result[0], result[1], "link#12")]:
                with self.assertRaises(ProtocolError):
                    network_identity("en5", ip, mac, gateway)
            with self.assertRaises(ProtocolError):
                network_identity("en0;bad", *result)
            run.assert_not_called()

    @patch("esurfing.network.sys.platform", "darwin")
    def test_placeholder_mac_is_rejected_but_real_override_is_accepted(self):
        hidden = IFCONFIG.replace("02:ab:cd:12:34:56", "02:00:00:00:00:00")
        with patch("esurfing.network.system_output", return_value=hidden) as run:
            with self.assertRaisesRegex(ProtocolError, "placeholder"):
                network_identity("en0")
            run.assert_called_once_with(["/sbin/ifconfig", "en0"])
        with patch("esurfing.network.system_output", side_effect=[hidden, ROUTE]):
            self.assertEqual(network_identity("en0", mac="02:ab:cd:12:34:56")[1], "02-AB-CD-12-34-56")

    @patch("esurfing.network.sys.platform", "linux")
    def test_linux_keeps_gateway_selection_by_interface_and_metric(self):
        routes = ("Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
                  "eth0 00000000 016433C6 0003 0 0 0 00000000\n"
                  "eth1 00000000 FE0200C0 0003 0 0 100 00000000\n"
                  "eth1 00000000 010200C0 0003 0 0 10 00000000\n")
        def read(path):
            return "02:00:00:00:00:01\n" if path.name == "address" else routes
        with patch.object(Path, "read_text", read):
            self.assertEqual(network_identity("eth1", ip="192.0.2.2"),
                             ("192.0.2.2", "02-00-00-00-00-01", "192.0.2.1"))

    def test_command_failures_are_bounded_and_sanitized(self):
        for result in [subprocess.TimeoutExpired("route", 5), FileNotFoundError(),
                       subprocess.CompletedProcess([], 1, "", "private data")]:
            with patch("esurfing.network.subprocess.run") as run:
                if isinstance(result, Exception):
                    run.side_effect = result
                else:
                    run.return_value = result
                with self.assertRaises(ProtocolError) as caught:
                    system_output(["/sbin/route", "-n", "get", "default"])
                self.assertNotIn("private data", str(caught.exception))
                self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_network_info_does_not_create_state_or_contact_portal(self):
        output = io.StringIO()
        with patch("esurfing.__main__.network_identity", return_value=("192.0.2.2", "02-00-00-00-00-01", "192.0.2.1")), \
             patch("esurfing.__main__.Transport") as transport, \
             patch("esurfing.__main__.stable_client_id") as state, \
             contextlib.redirect_stdout(output):
            self.assertEqual(main(["network-info", "--interface", "en0"]), 0)
        self.assertEqual(json.loads(output.getvalue())["interface"], "en0")
        transport.assert_not_called()
        state.assert_not_called()


class BoundConnectionTests(unittest.TestCase):
    def test_macos_connect_binds_device_then_source_ip(self):
        sock = Mock()
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 80))]
        with patch("esurfing.network.socket.if_nametoindex", return_value=12), \
             patch("esurfing.network.socket.getaddrinfo", return_value=addresses) as lookup, \
             patch("esurfing.network.socket.socket", return_value=sock):
            self.assertIs(macos_connection(("portal.example", 80), 15, ("192.0.2.2", 0), interface="en0"), sock)
        lookup.assert_called_once_with(b"portal.example", 80, socket.AF_INET, socket.SOCK_STREAM)
        self.assertEqual(sock.mock_calls, [call.settimeout(15),
                         call.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_BOUND_IF", 25), 12),
                         call.bind(("192.0.2.2", 0)), call.connect(("192.0.2.1", 80))])

    def test_non_ascii_hostname_is_rejected_before_dns(self):
        with patch("esurfing.network.socket.getaddrinfo") as lookup:
            with self.assertRaises(ProtocolError):
                ipv4_connection(("中文.example", 80), 5, ("192.0.2.2", 0))
        lookup.assert_not_called()

    def test_https_passes_ascii_bytes_and_retains_certificate_verification(self):
        import ssl
        connection = ASCIIHTTPSConnection("portal.example", timeout=5)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        raw_socket = Mock()
        connection.sock = raw_socket
        with patch("http.client.HTTPConnection.connect"), patch.object(connection._context, "wrap_socket") as wrap:
            connection.connect()
        wrap.assert_called_once_with(raw_socket, server_hostname=b"portal.example")

    def test_binding_failure_closes_socket_without_unbound_fallback(self):
        sock = Mock()
        sock.setsockopt.side_effect = PermissionError("denied")
        with patch("esurfing.network.socket.if_nametoindex", return_value=12), \
             patch("esurfing.network.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 80))]), \
             patch("esurfing.network.socket.socket", return_value=sock):
            with self.assertRaises(PermissionError):
                macos_connection(("portal.example", 80), 15, ("192.0.2.2", 0), interface="en0")
        sock.close.assert_called_once()
        sock.connect.assert_not_called()

    @patch("esurfing.client.sys.platform", "darwin")
    def test_http_and_https_install_bound_factory(self):
        for scheme, connection in [("http", "http.client.HTTPConnection"), ("https", "ASCIIHTTPSConnection")]:
            with patch("esurfing.client." + connection) as constructor:
                instance = constructor.return_value
                instance.getresponse.return_value.status = 200
                instance.getresponse.return_value.getheaders.return_value = []
                instance.getresponse.return_value.read.return_value = b"OK"
                response = Transport("192.0.2.2", interface="en0").request("GET", scheme+"://portal.example/", {})
                self.assertEqual(response.body, b"OK")
                self.assertEqual(instance._create_connection.keywords, {"interface": "en0"})
                instance.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
