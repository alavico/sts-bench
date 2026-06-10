import pytest

from sts_bench.env import ConnectionClosed


def test_lines_travel_both_directions(link):
    conn, mod = link
    conn.send_line("ready")
    assert mod.expect_line() == "ready"

    mod.sock.sendall(b'{"hello": 1}\n')
    assert conn.recv_line(timeout=2) == '{"hello": 1}'


def test_poll_line_returns_none_on_timeout(link):
    conn, _ = link
    assert conn.poll_line(timeout=0.05) is None


def test_recv_after_peer_close_raises(link):
    conn, mod = link
    mod.close()
    with pytest.raises(ConnectionClosed):
        conn.recv_line(timeout=1)


def test_partial_line_not_delivered_until_newline(link):
    conn, mod = link
    mod.sock.sendall(b'{"partial"')
    assert conn.poll_line(timeout=0.05) is None
    mod.sock.sendall(b': true}\n')
    assert conn.recv_line(timeout=2) == '{"partial": true}'
