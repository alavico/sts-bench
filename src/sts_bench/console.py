"""Interactive brain. Runs in YOUR terminal; drives the game over a socket.

This is the long-lived process the relay connects to. Two modes:

    uv run python -m sts_bench.console            # manual: you type commands
    uv run python -m sts_bench.console --auto     # spirecomm's SimpleAgent plays

Workflow: start this first (it listens), then trigger CommunicationMod's
external process in-game. The mod launches relay.py, which connects here.

In manual mode you type raw CommunicationMod commands, e.g.:

    state            refresh the current state
    play 0 1         play hand card 0 targeting monster 1
    play 2           play hand card 2 (no target)
    end              end turn
    choose 0         pick choice 0 on the current screen
    proceed          click the right-side button
    start ironclad   begin a run
    q                quit the console

The protocol travels over the socket; this terminal's stdin/stdout stay yours,
so input() reads your keyboard and prints land here, not in the game.
"""

import argparse
import json
import socket
import sys

HOST, PORT = "127.0.0.1", 9999


def serve_one():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    print(
        f"[brain] listening on {HOST}:{PORT} -- now trigger "
        f"CommunicationMod's external process in-game.",
        file=sys.stderr,
    )
    conn, addr = srv.accept()
    srv.close()
    print(f"[brain] relay connected from {addr}.", file=sys.stderr)
    return conn


def format_state(d):
    """Compact human-readable digest of a CommunicationMod state message."""
    cmds = d.get("available_commands", [])
    gs = d.get("game_state")
    if not gs:
        return f"(out of game)  commands: {cmds}"

    lines = [
        f"floor {gs.get('floor')} act {gs.get('act')}  "
        f"HP {gs.get('current_hp')}/{gs.get('max_hp')}  gold {gs.get('gold')}  "
        f"screen={gs.get('screen_type')}"
    ]

    cb = gs.get("combat_state")
    if cb:
        p = cb.get("player", {})
        powers = [(pw.get("power_name") or pw.get("id"), pw.get("amount")) for pw in p.get("powers", [])]
        lines.append(f"  player: energy {p.get('energy')}  block {p.get('block')}  powers {powers}")
        for i, m in enumerate(cb.get("monsters", [])):
            if m.get("is_gone"):
                continue
            dmg, hits = m.get("move_adjusted_damage"), m.get("move_hits")
            threat = f"{dmg}x{hits}" if dmg not in (None, -1) else "?"
            mpowers = [(pw.get("power_name") or pw.get("id"), pw.get("amount")) for pw in m.get("powers", [])]
            lines.append(
                f"  monster[{i}] {m.get('name')} {m.get('current_hp')}/{m.get('max_hp')} "
                f"blk {m.get('block')}  intent {m.get('intent')} ({threat})  {mpowers}"
            )
        for i, c in enumerate(cb.get("hand", [])):
            tag = " [target]" if c.get("has_target") else ""
            up = "+" * c.get("upgrades", 0)
            lines.append(f"  hand[{i}] ({c.get('cost')}) {c.get('name')}{up}{tag}")

    choices = gs.get("choice_list")
    if choices:
        lines.append("  choices: " + "  ".join(f"[{i}] {c}" for i, c in enumerate(choices)))

    relics = gs.get("relics", [])
    if relics:
        lines.append("  relics: " + ", ".join(r.get("name") for r in relics))

    potions = [p.get("name") for p in gs.get("potions", []) if p.get("name") != "Potion Slot"]
    if potions:
        lines.append("  potions: " + ", ".join(potions))

    lines.append(f"  commands: {cmds}")
    return "\n".join(lines)


def manual(conn):
    f = conn.makefile("rwb", buffering=0)
    f.write(b"ready\n")  # CommunicationMod waits for this before sending state
    print("[brain] sent 'ready'. Empty input = 'state'. 'q' to quit.\n", file=sys.stderr)
    while True:
        line = f.readline()
        if not line:
            print("[brain] relay disconnected.", file=sys.stderr)
            return
        try:
            print("\n" + format_state(json.loads(line.decode("utf-8"))))
        except json.JSONDecodeError:
            print("\n[raw] " + line.decode("utf-8", "replace").rstrip())
        try:
            cmd = input("> ").strip()
        except EOFError:
            return
        if cmd in ("q", "quit", "exit"):
            return
        f.write(((cmd or "state") + "\n").encode("utf-8"))


def auto(conn):
    import itertools

    from spirecomm.communication.coordinator import Coordinator
    from spirecomm.ai.agent import SimpleAgent
    from spirecomm.spire.character import PlayerClass

    # Route the protocol over the socket; logging via stderr stays in the terminal.
    sys.stdin = conn.makefile("r", encoding="utf-8")
    sys.stdout = conn.makefile("w", encoding="utf-8")

    agent = SimpleAgent()
    coordinator = Coordinator()
    coordinator.signal_ready()
    coordinator.register_command_error_callback(agent.handle_error)
    coordinator.register_state_change_callback(agent.get_next_action_in_game)
    coordinator.register_out_of_game_callback(agent.get_next_action_out_of_game)
    for chosen_class in itertools.cycle(PlayerClass):
        agent.change_class(chosen_class)
        coordinator.play_one_game(chosen_class)


def main():
    parser = argparse.ArgumentParser(description="Interactive Slay the Spire brain.")
    parser.add_argument("--auto", action="store_true", help="let SimpleAgent play instead of you")
    args = parser.parse_args()

    conn = serve_one()
    try:
        (auto if args.auto else manual)(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
