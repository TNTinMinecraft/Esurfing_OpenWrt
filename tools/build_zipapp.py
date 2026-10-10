"""Build a transferable zipapp containing only client source, never captures/secrets."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
target = root / "esurfing.pyz"
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
    archive.writestr("__main__.py", "from esurfing.__main__ import main\nraise SystemExit(main())\n")
    for source in sorted((root / "esurfing").glob("*.py")):
        archive.write(source, source.relative_to(root))
print(f"Built {target.name}: {target.stat().st_size} bytes")
