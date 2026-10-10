"""Save only the initial algorithm download, never authentication payloads."""
import hashlib
import os
from pathlib import Path
from .crypto import Algorithm, ProtocolError, MAX_PACKAGE


def save_algorithm(path, package):
    if not package or len(package) > MAX_PACKAGE:
        raise ProtocolError("Invalid algorithm download size")
    # Exclusive creation also rejects symlinks; never overwrite an existing diagnostic.
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ProtocolError("Algorithm output already exists; choose another --output filename") from None
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(package)
    return {"saved_to": str(Path(path).resolve()), "package_bytes": len(package),
            "sha256": hashlib.sha256(package).hexdigest()}


def inspect_algorithm(package):
    algorithm = Algorithm.from_package(package)
    return {"algorithm_id": algorithm.identifier, "cipher": algorithm.cipher,
            "template_supported": True}
