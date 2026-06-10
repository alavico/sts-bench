import pytest

from sts_bench.env import CommunicationModEnv, EnvError


def make_env(conn, timeout=2.0):
    return CommunicationModEnv(conn, step_timeout=timeout)


def handshaken(conn, mod, **state_overrides):
    env = make_env(conn)
    mod.send_state(**state_overrides)
    env.handshake()
    assert mod.expect_line() == "ready"
    return env


def test_handshake_sends_ready_and_returns_first_state(link):
    conn, mod = link
    env = make_env(conn)
    sent = mod.send_state()
    state = env.handshake()
    assert mod.expect_line() == "ready"
    assert state == sent
    assert env.legal_actions() == ["play", "end", "state"]


def test_step_returns_next_ready_state(link):
    conn, mod = link
    env = handshaken(conn, mod)
    mod.send_state(game_state={"floor": 2, "act": 1, "screen_type": "NONE"})
    result = env.step("end")
    assert mod.expect_line() == "end"
    assert result.ok
    assert result.state["game_state"]["floor"] == 2


def test_step_skips_not_ready_messages(link):
    conn, mod = link
    env = handshaken(conn, mod)
    mod.send_state(ready_for_command=False, game_state={"floor": 1, "screen_type": "NONE"})
    mod.send_state(game_state={"floor": 2, "act": 1, "screen_type": "NONE"})
    result = env.step("proceed")
    assert result.ok
    assert result.state["game_state"]["floor"] == 2


def test_step_drains_to_latest_ready_state(link):
    conn, mod = link
    env = handshaken(conn, mod)
    mod.send_state(game_state={"floor": 2, "act": 1, "screen_type": "NONE"})
    mod.send_state(game_state={"floor": 3, "act": 1, "screen_type": "NONE"})
    result = env.step("proceed")
    assert result.state["game_state"]["floor"] == 3


def test_rejected_command_keeps_previous_state(link):
    conn, mod = link
    env = handshaken(conn, mod)
    before = env.state
    mod.send_json({"error": "Selected card cannot be played", "ready_for_command": True})
    result = env.step("play 1")
    assert not result.ok
    assert result.error == "Selected card cannot be played"
    assert result.state == before
    assert env.state == before


def test_step_timeout_nudges_with_state_then_raises(link):
    conn, mod = link
    env = handshaken(conn, mod)
    env._step_timeout = 0.3
    with pytest.raises(TimeoutError):
        env.step("end")
    assert mod.expect_line() == "end"
    assert mod.expect_line() == "state"  # the halfway nudge


def test_reset_requires_out_of_game(link):
    conn, mod = link
    env = handshaken(conn, mod, in_game=True)
    with pytest.raises(EnvError, match="mid-run"):
        env.reset(seed="ABC")


def test_reset_sends_start_with_seed(link):
    conn, mod = link
    env = handshaken(conn, mod, in_game=False, available_commands=["start", "state"], game_state=None)
    mod.send_state(in_game=True)
    state = env.reset(character="IRONCLAD", ascension=0, seed="STSBENCH1")
    assert mod.expect_line() == "start IRONCLAD 0 STSBENCH1"
    assert state["in_game"] is True


def test_step_before_handshake_raises(link):
    conn, _ = link
    env = make_env(conn)
    with pytest.raises(EnvError, match="handshake"):
        env.step("state")
