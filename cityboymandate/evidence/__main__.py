"""Check a document library:  python -m cityboymandate.evidence check ./library [--allow-sample]"""
import json
import sys
from cityboymandate.evidence.library import DocumentLibrary

if len(sys.argv) >= 3 and sys.argv[1] == "check":
    lib = DocumentLibrary(sys.argv[2], allow_sample="--allow-sample" in sys.argv)
    print(json.dumps(lib.status(), indent=2))
    sys.exit(1 if lib.problems else 0)
print(__doc__)
sys.exit(2)
