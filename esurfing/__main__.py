import argparse
import getpass
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import time
import uuid
from .client import Client, Identity, Transport, NetworkError, discover, local_time
from .crypto import ProtocolError
from .network import network_identity
from .runtime import check_runtime


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def stable_client_id(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "client-id"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(uuid.uuid4()) + "\n")
    try:
        return str(uuid.UUID(path.read_text().strip()))
    except ValueError:
        raise ProtocolError("Invalid client-id state file") from None


def credentials(path):
    if path:
        # Credentials are optional user-created input, never copied from the capture.
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ProtocolError("Credentials file must have mode 600 (chmod 600 FILE)")
        data = json.loads(path.read_text())
        username, password = data.get("username"), data.get("password")
    else:
        username = input("宽带账号: ").strip()
        password = getpass.getpass("宽带密码: ")
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        raise ProtocolError("Nonempty username and password are required")
    return username, password


def network_arguments(parser):
    parser.add_argument("--interface", required=True,
                        help="实际网卡设备：macOS 如 en0/en5，Linux/OpenWrt 如 eth1/eth0.2")
    parser.add_argument("--ip", help="覆盖自动读取的 IPv4 地址")
    parser.add_argument("--mac", help="覆盖协议中报告的 MAC；不修改网卡 MAC")
    parser.add_argument("--gateway", help="覆盖自动读取的 IPv4 网关")


def main(argv=None):
    parser = argparse.ArgumentParser(description="江苏天翼校园 Python 实验客户端（尚未线路实测）")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify-pcap", help="只做离线校验，不发送网络请求")
    verify.add_argument("capture", type=Path)
    verify.add_argument("--tshark")
    verify.add_argument("--server", default="221.228.39.88")
    commands.add_parser("check-runtime", help="检查 IDNA、LZMA、SSL 标准库，不发送网络请求")
    info = commands.add_parser("network-info", help="只读取指定网卡的信息，不发送认证请求")
    network_arguments(info)
    login = commands.add_parser("login", help="从指定接口登录并前台保活；Ctrl+C 注销")
    fetch = commands.add_parser("fetch-algorithm", help="只下载并保存认证前的算法包，不询问或发送账号密码")
    fetch.add_argument("--output", type=Path, required=True, help="保存原始算法包；拒绝覆盖已有文件")
    inspect = commands.add_parser("inspect-algorithm", help="离线检查已保存的算法包")
    inspect.add_argument("package", type=Path)
    for network_parser in (login, fetch):
        network_arguments(network_parser)
        network_parser.add_argument("--portal-url", help="当前线路门户入口；默认 preportal.go189.cn/detect.html")
        network_parser.add_argument("--ticket-url", help="手工覆盖：必须同时提供 --auth-url")
        network_parser.add_argument("--auth-url")
        network_parser.add_argument("--client-id", type=uuid.UUID)
        network_parser.add_argument("--state-dir", type=Path, default=Path.home()/".config"/"esurfing")
        network_parser.add_argument("--hostname", default=socket.gethostname())
        network_parser.add_argument("--ostag", default="Windows 8(9200),64 bit ", help="协议兼容用的 ostag，默认与抓包相同")
        network_parser.add_argument("--timeout", type=float, default=15)
    login.add_argument("--credentials", type=Path, help="可选 mode 600 JSON 文件；省略则交互输入")
    login.add_argument("--local-time", help="仅调试用：固定九字符计数值；默认单调时钟近似原 RDTSC")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect-algorithm":
            from .diagnostics import inspect_algorithm
            from .crypto import MAX_PACKAGE
            with args.package.open("rb") as stream:
                package = stream.read(MAX_PACKAGE+1)
            print(json.dumps(inspect_algorithm(package), indent=2))
            return 0
        if args.command == "check-runtime":
            runtime = check_runtime()
            log("Python 运行依赖检查通过：ASCII 域名解析、LZMA、SSL。")
            if not runtime["idna"]:
                log("IDNA/unicodedata 不可用；当前 ASCII 门户域名使用无需该模块的解析路径。")
            return 0
        if args.command == "verify-pcap":
            from .capture import verify_capture
            print(json.dumps(verify_capture(args.capture, args.tshark, args.server), indent=2, ensure_ascii=False))
            return 0
        if args.command == "network-info":
            ip, mac, gateway = network_identity(args.interface, args.ip, args.mac, args.gateway)
            print(json.dumps({"platform": sys.platform, "interface": args.interface,
                              "ipv4": ip, "mac": mac, "gateway": gateway}, indent=2))
            return 0
        if bool(args.ticket_url) != bool(args.auth_url):
            raise ProtocolError("--ticket-url and --auth-url must be supplied together")
        if args.timeout <= 0 or args.timeout > 120:
            raise ProtocolError("--timeout must be between 0 and 120 seconds")
        if getattr(args, "local_time", None) and not re.fullmatch(r"[0-o]{9}", args.local_time):
            raise ProtocolError("--local-time must contain nine characters in ASCII range 0x30..0x6f")
        if args.command == "login":
            check_runtime()
        if args.command == "fetch-algorithm" and args.output.exists():
            raise ProtocolError("Algorithm output already exists; choose another --output filename")
        ip, mac, gateway = network_identity(args.interface, args.ip, args.mac, args.gateway)
        identity = Identity(ip, mac, gateway, str(args.client_id) if args.client_id else stable_client_id(args.state_dir),
                            args.hostname, args.ostag)
        transport = Transport(ip, args.timeout, interface=args.interface)
        log(f"实验客户端：interface={args.interface}, IPv4={ip}, gateway={gateway}")
        log("额外检测协议尚未实现；local-time 使用单调时钟近似值。")
        if args.ticket_url:
            ticket_url, auth_url = args.ticket_url, args.auth_url
        else:
            from .client import DEFAULT_DETECT
            log("正在发现当前线路的认证入口…")
            ticket_url, auth_url = discover(transport, args.portal_url or DEFAULT_DETECT)
        clock = (lambda: args.local_time) if getattr(args, "local_time", None) else local_time
        client = Client(transport, identity, ticket_url, auth_url, clock)
        if args.command == "fetch-algorithm":
            from .diagnostics import save_algorithm, inspect_algorithm
            package = client.download_algorithm()
            print(json.dumps(save_algorithm(args.output, package), indent=2), flush=True)
            # Save before inspection so unknown/truncated algorithm data remains available.
            print(json.dumps(inspect_algorithm(package), indent=2), flush=True)
            return 0
        log("正在协商并解析算法包…")
        client.negotiate()
        log(f"算法包已识别：{client.algorithm.identifier} ({client.algorithm.cipher})")
        username, password = credentials(args.credentials)

        def interrupted(signum, frame):
            raise KeyboardInterrupt()

        signal.signal(signal.SIGTERM, interrupted)
        outcome = 0
        try:
            expiry = client.login(username, password)
            password = None
            username = None
            log(f"auth 返回成功，ticket expire={expiry}s；开始首个保活（尚未验证互联网连通性）。")
            while True:
                interval = None
                for attempt in range(client.keep_retry):
                    try:
                        interval = client.keep()
                        break
                    except NetworkError:
                        if attempt + 1 == client.keep_retry:
                            raise
                        log(f"保活网络请求失败，稍后重试 {attempt+1}/{client.keep_retry}。")
                        time.sleep(min(5 * (attempt+1), 30))
                log(f"保活成功；下一次间隔 {interval}s。Ctrl+C 可注销。")
                time.sleep(interval)
        except KeyboardInterrupt:
            log("正在结束会话…")
        finally:
            if client.active:
                try:
                    client.logout()
                    log("注销成功。")
                except (ProtocolError, KeyboardInterrupt):
                    log("注销未确认；请检查原客户端/门户的在线状态。")
                    outcome = 1
        return outcome
    except (ProtocolError, OSError, ValueError, EOFError) as exc:
        # Built-in exceptions can echo user input or file content; keep them type-only.
        log(str(exc) if isinstance(exc, ProtocolError) else f"本地输入/系统错误：{type(exc).__name__}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
