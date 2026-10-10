"""Offline comparison against the user's capture; never prints/writes credentials."""
import ipaddress
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from .crypto import Algorithm, ProtocolError, PROBE, checksum
from .client import (Identity, parse_xml, ticket_request, auth_request, keep_request)


def read_capture(path, tshark=None, server="221.228.39.88"):
    ipaddress.IPv4Address(server)
    tshark = tshark or shutil.which("tshark")
    if not tshark and Path("/Applications/Wireshark.app/Contents/MacOS/tshark").exists():
        tshark = "/Applications/Wireshark.app/Contents/MacOS/tshark"
    if not tshark:
        raise ProtocolError("Wireshark tshark is required for offline capture verification")
    result = subprocess.run([tshark, "-r", str(path), "-Y", f"http && ip.addr == {server}",
                             "-T", "json", "-J", "frame http", "--no-duplicate-keys"],
                            capture_output=True, timeout=120)
    if result.returncode:
        raise ProtocolError("tshark could not read the capture")
    packets = json.loads(result.stdout)
    records = []
    for packet in packets:
        layers = packet["_source"]["layers"]
        http = layers["http"]
        if "http.file_data" not in http:
            continue
        lines = http.get("http.request.line", http.get("http.response.line", []))
        if isinstance(lines, str):
            lines = [lines]
        headers = {}
        for line in lines:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.lower()] = v.replace("\\r\\n", "").strip()
        records.append({
            "frame": int(layers["frame"]["frame.number"]),
            "url": http.get("http.request.full_uri") if "http.request" in http else None, "headers": headers,
            "body": bytes.fromhex(http["http.file_data"].replace(":", "")),
        })
    return records


def verify_capture(path, tshark=None, server="221.228.39.88"):
    records = read_capture(path, tshark, server)
    packages = [r for r in records if r["body"].startswith(b"001")]
    if len(packages) != 1:
        raise ProtocolError("This verifier expects one algorithm exchange, as in dump1.pcapng")
    algorithm = Algorithm.from_package(packages[0]["body"])
    decoded = []
    checksums = 0
    probe_count = 0
    for record in records:
        body = record["body"]
        if record["url"]:
            if record["headers"].get("cdc-checksum", "").lower() != checksum(body):
                raise ProtocolError(f"Frame {record['frame']}: checksum mismatch")
            checksums += 1
        if body == PROBE:
            probe_count += 1
            continue
        if record is packages[0]:
            continue
        plain = algorithm.decrypt(body)
        if algorithm.encrypt(plain) != body:
            raise ProtocolError(f"Frame {record['frame']}: ciphertext comparison failed")
        root = parse_xml(plain, "request" if record["url"] else "response")
        decoded.append((record, plain, root))
    if len(decoded) != 6 or checksums != 4 or probe_count != 1:
        raise ProtocolError("Capture does not contain the expected four requests and three XML responses")
    identity = None
    built = 0
    ticket_value = None
    expiry = interval = None
    stages = set()
    for record, plain, root in decoded:
        if not record["url"]:
            if root.find("ticket") is not None:
                ticket_value = root.findtext("ticket")
                expiry = int(root.findtext("expire"))
            if root.find("interval") is not None:
                interval = int(root.findtext("interval"))
            continue
        stage = urlsplit(record["url"]).path
        stamp = root.findtext("local-time")
        if stage.endswith("/ticket.cgi"):
            identity = Identity(root.findtext("ipv4"), root.findtext("mac"), root.findtext("gwip"),
                                root.findtext("client-id"), root.findtext("host-name"), root.findtext("ostag"),
                                root.findtext("ipv6") or "")
            rebuilt = ticket_request(identity, stamp)
        elif stage.endswith("/auth.cgi") and identity:
            if ticket_value != root.findtext("ticket"):
                raise ProtocolError("Ticket response/request mismatch")
            rebuilt = auth_request(identity, stamp, ticket_value, root.findtext("userid"), root.findtext("passwd"))
        elif stage.endswith("/keep.cgi") and identity:
            if ticket_value != root.findtext("ticket"):
                raise ProtocolError("Keep ticket mismatch")
            ports = (root.findtext("excheck-args/hello"), root.findtext("excheck-args/conn"))
            rebuilt = keep_request(identity, stamp, ticket_value, ports if all(ports) else None)
        else:
            raise ProtocolError("Unexpected request stage in capture")
        if rebuilt != plain:
            raise ProtocolError(f"Frame {record['frame']}: XML construction mismatch")
        built += 1
        stages.add(stage.rsplit("/", 1)[-1])
    if stages != {"ticket.cgi", "auth.cgi", "keep.cgi"} or expiry is None or interval is None:
        raise ProtocolError("Missing expected protocol stages")
    return {
        "algorithm_id": algorithm.identifier,
        "encrypted_messages_verified": len(decoded),
        "request_xml_verified": built,
        "request_checksums_verified": checksums,
        "ticket_expire_seconds": expiry,
        "keep_interval_seconds": interval,
        "verification": "offline only; live keep omits optional excheck-args",
    }
