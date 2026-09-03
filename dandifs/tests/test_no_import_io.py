"""Ensure importing dandifs performs no network I/O and constructs no client."""

import socket
import subprocess
import sys


def test_import_makes_no_network_calls():
    code = (
        "import socket\n"
        "class Guard(socket.socket):\n"
        "    def connect(self, *a, **k):\n"
        "        raise AssertionError('network access at import: %r' % (a,))\n"
        "socket.socket = Guard\n"
        "import dandifs\n"
        "from dandifs import DandiFileSystem\n"
        "assert 'dandi' in dandifs.__all__[0].lower()\n"
        "print('ok', dandifs.__version__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ok")


def test_construction_makes_no_network_calls():
    # Constructing a filesystem must not create a session or hit the network.
    orig_connect = socket.socket.connect

    def guard(self, *a, **k):
        raise AssertionError("network access during construction")

    socket.socket.connect = guard
    try:
        from dandifs import DandiFileSystem

        fs = DandiFileSystem("000026", skip_instance_cache=True)
        assert fs.dandiset == "000026"
        assert fs._session is None
    finally:
        socket.socket.connect = orig_connect
