"""Candidate shasum subset for the copied Forge helpers' SHA-256 calls."""

import hashlib
import sys


def main():
    if sys.argv[1:3] != ["-a", "256"] or len(sys.argv) not in (3, 4):
        raise SystemExit("Candidate shasum supports only -a 256 [FILE]")
    name = sys.argv[3] if len(sys.argv) == 4 else "-"
    if name == "-":
        result = hashlib.file_digest(sys.stdin.buffer, "sha256")
    else:
        with open(name, "rb") as stream:
            result = hashlib.file_digest(stream, "sha256")
    print(result.hexdigest() + "  " + name)


if __name__ == "__main__":
    main()
