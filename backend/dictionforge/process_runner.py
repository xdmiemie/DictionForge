"""Handshake prevents model startup before assignment to the Windows job object."""
import subprocess
import sys

if __name__ == "__main__":
    if sys.stdin.buffer.readline() != b"go\n":
        raise SystemExit(2)
    raise SystemExit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))
