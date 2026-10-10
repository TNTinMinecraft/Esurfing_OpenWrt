"""Check standard-library components split into separate OpenWrt packages."""
import codecs
import importlib
from .crypto import ProtocolError

IDNA_HELP = ("Python IDNA codec is unavailable. Install matching python3-codecs "
             "(and check python3-base/encodings.idna); then run check-runtime. "
             "The pip package idna does not replace the standard-library codec.")


def check_runtime():
    problems = []
    idna_available = True
    try:
        importlib.import_module("encodings.idna")
        importlib.import_module("unicodedata")
        # Exercise the actual codec path used by socket.getaddrinfo, without DNS/network I/O.
        codecs.encode("preportal.go189.cn", "idna")
    except (ImportError, LookupError):
        idna_available = False
    for module, package in [("lzma", "python3-lzma"), ("ssl", "python3-openssl")]:
        try:
            importlib.import_module(module)
        except ImportError:
            problems.append(f"Python {module} is unavailable; install matching {package}.")
    if problems:
        raise ProtocolError(" ".join(problems))
    return {"idna": idna_available, "ascii_dns": True, "lzma": True, "ssl": True}
