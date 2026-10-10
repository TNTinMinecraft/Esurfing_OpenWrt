"""Portal discovery, session state and XML construction for the observed Jiangsu flow."""
import html
from functools import partial
import http.client
import re
import socket
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from .crypto import Algorithm, ProtocolError, ZERO_ID, PROBE, checksum, MAX_PACKAGE
from .network import macos_connection, ipv4_connection
from .runtime import IDNA_HELP

USER_AGENT = "CCTP/WinSVR5/1069"
DEFAULT_DETECT = "http://preportal.go189.cn/detect.html"


class ServerError(ProtocolError):
    def __init__(self, stage, code):
        self.stage, self.code = stage, code
        # Never echo arbitrary response bodies: they can contain credentials or tickets.
        super().__init__(f"{stage}: server Error-Code={code}")


class NetworkError(ProtocolError):
    pass


def checked_url(url):
    try:
        parts = urlsplit(url)
        port = parts.port
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username or parts.password or parts.fragment
                or any(ord(c) < 33 or ord(c) > 126 for c in url)):
            raise ValueError()
        return parts
    except ValueError:
        raise ProtocolError("Invalid portal HTTP URL") from None


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class ASCIIHTTPSConnection(http.client.HTTPConnection):
    """Standard certificate-verified TLS, with an already-ASCII hostname.

    ssl accepts hostname bytes and decodes ASCII directly, avoiding its otherwise
    unconditional IDNA lookup. Certificate hostname verification remains enabled.
    """
    default_port = 443

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import ssl
        self._context = ssl.create_default_context()
        self._context.set_alpn_protocols(["http/1.1"])

    def connect(self):
        super().connect()
        hostname = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=hostname.encode("ascii"))


class Transport:
    def __init__(self, source_ip, timeout=15, interface=None):
        self.source_ip, self.timeout = source_ip, timeout
        self.interface = interface

    def request(self, method, url, headers, body=None):
        parts = checked_url(url)
        connection_type = ASCIIHTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(parts.hostname, parts.port, timeout=self.timeout,
                                     source_address=(self.source_ip, 0))
        connection._create_connection = ipv4_connection
        if sys.platform == "darwin" and self.interface:
            # Both HTTPConnection and HTTPSConnection call this factory before TLS.
            connection._create_connection = partial(macos_connection, interface=self.interface)
        target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read(MAX_PACKAGE+1)
            if len(payload) > MAX_PACKAGE:
                raise ProtocolError("Portal response exceeds size limit")
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, payload)
        except LookupError as exc:
            if "unknown encoding: idna" in str(exc).lower():
                raise ProtocolError(IDNA_HELP) from None
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise NetworkError(f"HTTP transport failed ({type(exc).__name__})") from None
        finally:
            connection.close()


class MetaRefresh(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refresh = None

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): v for k, v in attrs}
        if tag.lower() == "meta" and (attrs.get("http-equiv") or "").lower() == "refresh":
            self.refresh = attrs.get("content")


def refresh_target(value):
    match = re.search(r"(?:^|;)\s*url\s*=\s*(.*?)\s*$", value or "", re.I)
    return html.unescape(match.group(1).strip("\"'")) if match else None


def parse_xml(data, root):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ProtocolError("DTD/entity declarations are unsupported")
    try:
        element = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        raise ProtocolError("Invalid portal XML") from None
    if element.tag != root:
        raise ProtocolError(f"Expected XML root {root}")
    return element


def portal_config(body, current_url):
    text = body.decode("utf-8", errors="replace")
    marker = "//config.campus.js.chinatelecom.com"
    match = re.search(r"<!--" + re.escape(marker) + r"(.*?)" + re.escape(marker) + r"-->", text, re.S)
    candidates = [match.group(1)] if match else [text]
    for candidate in candidates:
        # Extract the two documented tags without assuming a particular XML wrapper.
        urls = []
        for tag in ("ticket-url", "auth-url"):
            found = re.search(r"<" + tag + r"(?:\s[^>]*)?>(.*?)</" + tag + r">", candidate, re.S)
            if not found:
                break
            value = found.group(1).strip()
            if value.startswith("<![CDATA[") and value.endswith("]]>"):
                value = value[9:-3]
            else:
                value = html.unescape(value)
            if not value:
                break
            value = urljoin(current_url, value.strip())
            checked_url(value)
            urls.append(value)
        if len(urls) == 2:
            return tuple(urls)
    return None


def discover(transport, start_url=DEFAULT_DETECT):
    current = start_url
    visited = set()
    for _ in range(12):
        checked_url(current)
        if current in visited:
            raise ProtocolError("Portal redirect loop; supply the current portal URL")
        visited.add(current)
        response = transport.request("GET", current, {"User-Agent": USER_AGENT, "Connection": "close"})
        if response.status in (301, 302, 303, 307, 308):
            target = response.headers.get("location")
        elif response.status == 200:
            config = portal_config(response.body, current)
            if config:
                return config
            parser = MetaRefresh()
            parser.feed(response.body.decode("utf-8", errors="replace"))
            target = refresh_target(response.headers.get("refresh")) or refresh_target(parser.refresh)
        else:
            raise ProtocolError(f"Portal discovery: HTTP {response.status}")
        if not target:
            raise ProtocolError("No portal configuration/redirect found; supply --portal-url or explicit endpoint URLs")
        current = urljoin(current, html.unescape(target))
    raise ProtocolError("Too many portal redirects")


def local_time(ticks=None):
    # Native DLL uses (RDTSC >> 34), formatted as nine 6-bit digits + ASCII 0x30.
    # ARM has no RDTSC: monotonic nanoseconds are a substitute, pending live validation.
    ticks = time.monotonic_ns() if ticks is None else ticks
    value = (ticks >> 34) & 0x3fffffff
    return "".join(chr(((value >> shift) & 63) + 0x30) for shift in range(48, -1, -6))


def request_xml(fields):
    def element(name, value):
        if isinstance(value, list):
            text = "".join(element(k, v) for k, v in value)
        else:
            text = str(value)
            if any((ord(c) < 32 and c not in "\t\n\r") or 0xd800 <= ord(c) <= 0xdfff for c in text):
                raise ProtocolError("Input contains invalid XML characters")
            text = html.escape(text, quote=False)
        return f"<{name}>{text}</{name}>"
    xml = '<?xml version="1.0" encoding="utf-8"?><request>\n'
    xml += "".join(element(k, v) + "\n" for k, v in fields)
    return (xml + "</request>").encode("utf-8")


@dataclass
class Identity:
    ip: str
    mac: str
    gateway: str
    client_id: str
    hostname: str
    ostag: str = "Windows 8(9200),64 bit "
    ipv6: str = ""

    def common(self):
        return [("client-id", self.client_id), ("user-agent", USER_AGENT)]

    def network(self):
        return [("mac", self.mac), ("ipv6", self.ipv6), ("ipv4", self.ip)]


def ticket_request(identity, stamp):
    return request_xml([("ostag", identity.ostag), ("local-time", stamp), ("gwip", identity.gateway)]
                       + identity.network() + identity.common() + [("host-name", identity.hostname)])


def auth_request(identity, stamp, ticket, username, password):
    return request_xml([("local-time", stamp), ("ticket", ticket), ("passwd", password), ("userid", username)]
                       + identity.common())


def keep_request(identity, stamp, ticket, ports=None):
    fields = [("local-time", stamp)]
    if ports:
        fields.append(("excheck-args", [("hello", ports[0]), ("conn", ports[1])]))
    return request_xml(fields + identity.network() + [("ticket", ticket)] + identity.common())


def term_request(identity, stamp, ticket):
    # PortalServer.dll builds reason=1 for user-requested disconnection.
    return request_xml([("reason", "1"), ("local-time", stamp), ("ticket", ticket)] + identity.common())


def required_text(root, tag):
    value = root.findtext(tag)
    if value is None or not value.strip():
        raise ProtocolError(f"Response is missing {tag}")
    return value.strip()


def bounded_integer(root, tag, default, low, high):
    value = root.findtext(tag)
    if value is None:
        if default is None:
            raise ProtocolError(f"Response is missing {tag}")
        return default
    try:
        number = int(value)
    except ValueError:
        raise ProtocolError(f"Invalid response {tag}") from None
    if not low <= number <= high:
        raise ProtocolError(f"Response {tag} is outside supported bounds")
    return number


def check_extra_commands(root):
    if root.find(".//excheck") is not None or root.find(".//zexcheck") is not None:
        raise ProtocolError("Server requested an unimplemented excheck/zexcheck command; more capture analysis is needed")


class Client:
    def __init__(self, transport, identity, ticket_url, auth_url, clock=local_time):
        checked_url(ticket_url)
        checked_url(auth_url)
        self.transport, self.identity = transport, identity
        self.ticket_url, self.auth_url = ticket_url, auth_url
        self.clock = clock
        self.algorithm = None
        self.ticket = None
        self.keep_url = self.term_url = None
        self.keep_retry = 5
        self.active = False

    def post(self, url, body, identifier):
        response = self.transport.request("POST", url, {
            "User-Agent": USER_AGENT, "Connection": "close", "Algo-ID": identifier,
            "Client-ID": self.identity.client_id, "CDC-Checksum": checksum(body),
        }, body)
        if response.status != 200:
            raise ProtocolError(f"Authentication endpoint returned HTTP {response.status}")
        code = response.headers.get("error-code", "")
        if not re.fullmatch(r"-?[0-9]{1,10}", code):
            raise ProtocolError("Missing/invalid Error-Code header")
        return int(code), response

    def download_algorithm(self):
        """Fetch the pre-auth algorithm only; no account, password or ticket is sent."""
        code, response = self.post(self.ticket_url, PROBE, ZERO_ID)
        if code != 200:
            raise ServerError("algorithm negotiation", code)
        return response.body

    def negotiate(self):
        self.algorithm = Algorithm.from_package(self.download_algorithm())

    def exchange(self, stage, url, make_xml):
        for _ in range(3):
            if self.algorithm is None:
                self.negotiate()
            body = self.algorithm.encrypt(make_xml())
            code, response = self.post(url, body, self.algorithm.identifier)
            if code == 200:
                self.algorithm = Algorithm.from_package(response.body)
                continue
            if code != 0:
                raise ServerError(stage, code)
            root = parse_xml(self.algorithm.decrypt(response.body), "response")
            check_extra_commands(root)
            return root
        raise ProtocolError("Repeated algorithm updates; stopping")

    def login(self, username, password):
        if self.active:
            raise ProtocolError("A session is already active")
        self.ticket = None
        self.keep_url = self.term_url = None
        response = self.exchange("ticket", self.ticket_url, lambda: ticket_request(self.identity, self.clock()))
        self.ticket = required_text(response, "ticket")
        self.ticket_expire = bounded_integer(response, "expire", None, 1, 30*86400)
        response = self.exchange("auth", self.auth_url, lambda: auth_request(
            self.identity, self.clock(), self.ticket, username, password))
        self.keep_url = urljoin(self.auth_url, required_text(response, "keep-url"))
        self.term_url = urljoin(self.auth_url, required_text(response, "term-url"))
        checked_url(self.keep_url)
        checked_url(self.term_url)
        self.keep_retry = bounded_integer(response, "keep-retry", 5, 1, 20)
        self.active = True
        return self.ticket_expire

    def keep(self):
        if not self.active:
            raise ProtocolError("No active session")
        response = self.exchange("keep", self.keep_url, lambda: keep_request(self.identity, self.clock(), self.ticket))
        level = bounded_integer(response, "level", None, 0, 1000)
        if level != 0:
            raise ProtocolError(f"Server returned unimplemented keep level={level}")
        return bounded_integer(response, "interval", None, 1, 86400)

    def logout(self):
        if not self.active:
            return
        # Some native term handlers use only the Error-Code header; do not require XML.
        for _ in range(3):
            body = self.algorithm.encrypt(term_request(self.identity, self.clock(), self.ticket))
            code, response = self.post(self.term_url, body, self.algorithm.identifier)
            if code == 200:
                self.algorithm = Algorithm.from_package(response.body)
                continue
            if code != 0:
                raise ServerError("term", code)
            self.active = False
            self.ticket = None
            return
        raise ProtocolError("Repeated algorithm updates during logout")
