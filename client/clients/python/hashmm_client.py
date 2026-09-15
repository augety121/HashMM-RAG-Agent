"""Compatibility import for the packaged HashMM Python client.

New code should use ``from hashmm.client import HashMMClient``.
"""
from hashmm.client import (  # noqa: F401
    HashMMClient,
    HashMMError,
    HashMMHTTPError,
    HashMMTransportError,
)


if __name__ == "__main__":
    import json
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:6006"
    key = sys.argv[2] if len(sys.argv) > 2 else ""
    c = HashMMClient(base, api_key=key)
    print("info:", json.dumps(c.info(), ensure_ascii=False, indent=2))
    print("rag :", json.dumps(c.rag("小米2024营收多少"), ensure_ascii=False, indent=2)[:800])
