"""Read current Linux/macOS interface settings and bind macOS IPv4 connections."""
import ipaddress
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
from .crypto import ProtocolError


def validate_interface(interface):
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
        raise ProtocolError("Invalid interface name")


def normalize_mac(mac):
    mac = mac.replace(":", "-").upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}-){5}[0-9A-F]{2}", mac):
        raise ProtocolError("Invalid MAC address")
    if mac in {"02-00-00-00-00-00", "00-00-00-00-00-00", "FF-FF-FF-FF-FF-FF"}:
        raise ProtocolError("MAC is a placeholder/unusable address; supply --mac with the current access interface's actual MAC")
    return mac


def system_output(argv):
    # Fixed executable paths and argument arrays: interface names never enter a shell.
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5,
                                env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired):
        raise ProtocolError(f"Cannot run {Path(argv[0]).name} to read network settings") from None
    if result.returncode:
        raise ProtocolError(
            f"{Path(argv[0]).name} could not read network settings; check the interface/permissions "
            "or supply --ip --mac --gateway")
    return result.stdout


def macos_identity(interface, ip, mac, gateway):
    if not ip or not mac:
        output = system_output(["/sbin/ifconfig", interface])
        if not ip:
            match = re.search(r"^\s*inet\s+([0-9.]+)\s", output, re.M)
            if not match:
                raise ProtocolError("No IPv4 address on the selected interface; connect to the access network first")
            ip = match.group(1)
        if not mac:
            match = re.search(r"^\s*ether\s+([0-9a-fA-F:]+)\s*$", output, re.M)
            if not match:
                raise ProtocolError("No Ethernet/Wi-Fi MAC on this interface; choose the access interface or supply --mac")
            mac = match.group(1)
    mac = normalize_mac(mac)
    if not gateway:
        output = system_output(["/sbin/route", "-n", "get", "-inet", "-ifscope", interface, "default"])
        fields = dict(re.findall(r"^\s*(gateway|interface):\s*(\S+)\s*$", output, re.M))
        if fields.get("interface") != interface:
            raise ProtocolError("Default route belongs to a different interface; supply --gateway for the selected interface")
        gateway = fields.get("gateway")
        if not gateway:
            raise ProtocolError("No IPv4 gateway for the selected interface; supply --gateway")
    return ip, mac, gateway


def linux_identity(interface, ip, mac, gateway):
    if not ip:
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            request = struct.pack("256s", interface.encode())
            ip = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), 0x8915, request)[20:24])
    if not mac:
        mac = Path("/sys/class/net", interface, "address").read_text().strip()
    if not gateway:
        routes = []
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            parts = line.split()
            if (len(parts) >= 8 and parts[0] == interface and parts[1] == "00000000"
                    and int(parts[3], 16) & 3 == 3):
                routes.append((int(parts[6]), socket.inet_ntoa(struct.pack("<I", int(parts[2], 16)))))
        if not routes:
            raise ProtocolError("No default gateway for interface; specify --gateway")
        gateway = min(routes)[1]
    return ip, mac, gateway


def network_identity(interface, ip=None, mac=None, gateway=None):
    validate_interface(interface)
    if sys.platform == "darwin":
        ip, mac, gateway = macos_identity(interface, ip, mac, gateway)
    elif sys.platform.startswith("linux"):
        ip, mac, gateway = linux_identity(interface, ip, mac, gateway)
    elif not all((ip, mac, gateway)):
        raise ProtocolError("Automatic network discovery supports Linux/macOS; supply --ip --mac --gateway")
    mac = normalize_mac(mac)
    try:
        ipaddress.IPv4Address(ip)
        ipaddress.IPv4Address(gateway)
    except ValueError:
        raise ProtocolError("Invalid IPv4 address or gateway") from None
    return ip, mac, gateway


def ipv4_connection(address, timeout, source_address, *, interface=None):
    """Resolve ASCII host bytes so CPython need not import the IDNA codec.

    URLs are ASCII-validated by checked_url. Never apply this to Unicode hostnames.
    The optional interface binding is macOS-specific; Linux retains source-IP binding.
    """
    try:
        host = address[0].encode("ascii")
    except UnicodeEncodeError:
        raise ProtocolError("Only ASCII/A-label authentication hostnames are supported") from None
    index = None
    if interface is not None:
        validate_interface(interface)
        index = socket.if_nametoindex(interface)
    addresses = socket.getaddrinfo(host, address[1], socket.AF_INET, socket.SOCK_STREAM)
    last_error = None
    for family, kind, protocol, _, destination in addresses:
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(timeout)
            if index is not None:
                # macOS SDK netinet/in.h defines IP_BOUND_IF=25.
                sock.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_BOUND_IF", 25), index)
            sock.bind(source_address)
            sock.connect(destination)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error:
        raise last_error
    raise OSError("No IPv4 address for the authentication endpoint")


def macos_connection(address, timeout, source_address, *, interface):
    return ipv4_connection(address, timeout, source_address, interface=interface)
