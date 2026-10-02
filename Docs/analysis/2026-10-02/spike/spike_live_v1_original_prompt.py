"""SPIKE (scratch only): 9 plain-prompt LLM agents playing the real AIwolf server in realtime.

Purpose: test whether "keep server/protocol, replace the AI brain with a simple agent" is feasible.
- Real server (server.network) with the standard_9 preset, authoritative ticks, private delivery.
- Agents are asyncio tasks in one process, each with its own WebSocket session.
- No fixed speaking order: each agent decides when to speak (random timer, sooner when addressed).
- The shared local llama-server queues generations (-np 1).
Not product code.  The only out-of-band input is the werewolf teammate list, which the server does
not deliver today (knows_teammates is declared in content but never sent).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
import unicodedata
from dataclasses import replace
from pathlib import Path
from random import Random
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "server" / "aiwolf_core").is_dir())  # repository root
import sys
sys.path.insert(0, str(ROOT))
from server.aiwolf_core import GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset
from server.network import GameRegistry, WebSocketGameServer, monotonic_seconds

LLM = "http://127.0.0.1:8090/v1/chat/completions"

ROLE_TEXT = {
    "werewolf": "Werewolf. Each night the werewolves secretly kill one player. The werewolf side wins when werewolves are equal to or outnumber everyone else.",
    "madman": "Madman. You are a human who secretly wants the werewolves to win. You do not know who the werewolves are, and the seer sees you as human.",
    "seer": "Seer. Each night you learn whether one player is a werewolf.",
    "medium": "Medium. Each night you learn whether the player executed that day was a werewolf.",
    "guard": "Guard. Each night you protect one player from the werewolf attack.",
    "villager": "Villager. You have no special ability.",
}
ROLE_NAME = {"werewolf": "werewolf", "madman": "madman", "seer": "seer", "medium": "medium", "guard": "guard", "villager": "villager"}
WOLF_SIDE = {"werewolf", "madman"}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


class Shared:
    def __init__(self, game_id: str, t0: float, language: str, temperature: float):
        self.game_id = game_id
        self.t0 = t0
        self.language = language
        self.temperature = temperature
        self.log: list[dict] = []          # everything public that happened, for the transcript
        self.calls: list[dict] = []        # LLM call stats
        self.rejections: list[dict] = []
        self.client = httpx.AsyncClient(timeout=180)
        self.sentence_counts: dict[str, int] = {}

    def now(self) -> float:
        return round(time.time() - self.t0, 1)


class Agent:
    def __init__(self, shared: Shared, pid: str, token: str, uri: str, teammates: list[str], seed: int):
        self.s = shared
        self.pid = pid
        self.token = token
        self.uri = uri
        self.rng = Random(seed)
        self.role = None
        self.teammates = teammates
        self.private: list[str] = []
        self.public_facts: list[str] = []
        self.transcript: list[tuple[int, str, str, str]] = []  # (day, channel, speaker, text)
        self.alive: set[str] = set()
        self.players: list[str] = []
        self.phase = None
        self.day = 0
        self.phase_ends_at = None
        self.server_offset = 0.0
        self.actions: list[dict] = []
        self.done = False
        self.mentioned = asyncio.Event()
        self.state_changed = asyncio.Event()
        self.spoke: dict[tuple[int, str], int] = {}
        self.last_text: str | None = None
        self.voted: set[int] = set()
        self.ability_used: set[int] = set()
        self.co_decided: set[int] = set()
        self.ws = None

    # ---------- protocol ----------
    def req(self, message_type: str, payload: dict) -> str:
        return json.dumps({"type": message_type, "protocol_version": "1.1", "event_id": str(uuid4()),
                           "game_id": self.s.game_id, "timestamp": 0, "payload": payload}, ensure_ascii=False)

    async def run(self):
        async with connect(self.uri, max_size=2**22) as ws:
            self.ws = ws
            await ws.send(self.req("session.join", {"entry_token": self.token}))
            await ws.send(self.req("session.ready", {}))
            behaviour = asyncio.create_task(self.behave())
            try:
                async for raw in ws:
                    self.on_message(json.loads(raw))
                    if self.done:
                        break
            finally:
                behaviour.cancel()

    def on_message(self, m: dict) -> None:
        t = m.get("type")
        p = m.get("payload") or {}
        ts = m.get("timestamp")
        if isinstance(ts, int):
            self.server_offset = ts - time.monotonic()
        if t == "game.state_sync":
            self.role = p["self"]["role_id"]
            self.players = [x["player_id"] for x in p["players"]]
            dead = {d["player_id"] for d in p.get("deaths", [])}
            self.alive = set(self.players) - dead
            self.on_actions(p.get("action_state") or {})
        elif t == "player.action_state":
            self.on_actions(p)
        elif t == "chat.message":
            msg = p["message"]
            speaker, text, channel = msg["player_id"], msg["message"], p["channel"]
            self.transcript.append((self.day, channel, speaker, text))
            if speaker != self.pid and re.search(rf"\b{re.escape(self.pid)}\b", text, re.I):
                self.mentioned.set()
            self.state_changed.set()
        elif t == "game.event":
            et, ep = p.get("event_type"), p.get("event_payload") or {}
            if et == "PHASE_STARTED":
                self.phase, self.day = ep["phase"], ep["day"]
                self.phase_ends_at = ep.get("phase_ends_at")
            elif et == "PLAYER_DIED":
                self.alive.discard(ep["player_id"])
                cause = {"lynched": "was executed by vote", "died_in_night": "was found dead in the morning"}.get(ep.get("public_cause"), f"died ({ep.get('public_cause')})")
                self.public_facts.append(f"Day {ep.get('day')}: {ep['player_id']} {cause}.")
            elif et == "VOTE_RESOLVED":
                tallies = {k: v for k, v in (ep.get("tallies") or {}).items() if v}
                result = ep.get("lynched_player_id") or ep.get("result")
                self.public_facts.append(f"Day {ep.get('day')} vote tallies: {tallies} -> {result}.")
            elif et == "CO_DECLARED":
                self.public_facts.append(f"Day {self.day}: {ep['player_id']} formally claimed to be the {ROLE_NAME.get(ep['claimed_role_id'], ep['claimed_role_id'])}.")
            elif et == "INSPECT_RESULT":
                self.private.append(f"Inspection result (received on day {self.day}): {ep['target_player_id']} is {'a WEREWOLF' if ep['result'] == 'wolf' else 'NOT a werewolf'}.")
            elif et == "MEDIUM_RESULT":
                self.private.append(f"Medium result: executed {ep.get('target_player_id')} was {'a WEREWOLF' if ep.get('result') == 'wolf' else 'NOT a werewolf'}.")
            elif et == "GAME_ENDED":
                self.done = True
            elif et and et not in {"PHASE_TIMING_CHANGED"}:
                pass
            self.state_changed.set()
        elif t == "action.rejected":
            self.s.rejections.append({"t": self.s.now(), "player": self.pid, "payload": p})

    def on_actions(self, a: dict) -> None:
        self.actions = a.get("actions") or []
        if a.get("phase"):
            self.phase, self.day = a["phase"], a.get("day", self.day)
        self.phase_ends_at = a.get("phase_ends_at", self.phase_ends_at)
        self.state_changed.set()

    def seconds_left(self) -> float:
        if not isinstance(self.phase_ends_at, int):
            return 0
        return self.phase_ends_at - (time.monotonic() + self.server_offset)

    def action(self, kind: str) -> dict | None:
        return next((x for x in self.actions if x.get("type") == kind), None)

    # ---------- prompting ----------
    def system(self) -> str:
        priv = list(self.private)
        if self.role == "werewolf" and self.teammates:
            priv.insert(0, f"Your fellow werewolf: {', '.join(self.teammates)}.")
        facts = "\n".join(f"- {x}" for x in priv) or "- (none)"
        goal = ("Help the werewolf side win. Never admit you are on the werewolf side; lying and fake role claims are allowed."
                if self.role in WOLF_SIDE else "Find the two werewolves and get them executed by the vote.")
        lang = "natural conversational English" if self.s.language == "en" else "自然な日本語の話し言葉（プレイヤー名は player-0 のように書く）"
        return (f"You are {self.pid}, a player in a 9-player game of Werewolf (a Mafia-style social deduction game). "
                "Players: player-0 to player-8. Roles in this game: 2 werewolves, 1 madman, 1 seer, 1 medium, 1 guard, 3 villagers.\n\n"
                f"Your secret role: {ROLE_TEXT[self.role]}\nPrivate facts only you know:\n{facts}\nYour goal: {goal}\n\n"
                "How to talk:\n"
                f"- Write ONE chat message of 1-3 short sentences in {lang}.\n"
                "- React to the discussion: answer questions aimed at you, push back on claims you doubt, or ask a specific player a pointed question.\n"
                "- Do not introduce yourself and do not restate your role in every message. Do not repeat what was already said.\n"
                "- Only state facts that appear in the public facts, the chat, or your private facts.\n"
                "- Output only the message text.")

    def situation(self, channel: str = "public") -> str:
        alive = ", ".join(sorted(self.alive))
        dead = sorted(set(self.players) - self.alive)
        facts = "\n".join(f"- {x}" for x in self.public_facts) or "- (nothing yet)"
        lines = [f"[Day {d}] {sp}: {tx}" for d, ch, sp, tx in self.transcript if ch == channel][-40:]
        chat = "\n".join(lines) or "(nobody has spoken yet today)"
        return (f"Current: Day {self.day}, phase: {self.phase} ({'daytime discussion' if self.phase == 'day' else self.phase}).\n"
                f"Alive: {alive}\nDead: {', '.join(dead) or 'none'}\n"
                f"Public facts (from the game master, always true):\n{facts}\n\n"
                f"Chat so far (oldest first):\n{chat}")

    async def llm(self, user: str, *, schema: dict | None = None, max_tokens: int = 160, purpose: str = "chat") -> str:
        body = {"model": "local", "messages": [{"role": "system", "content": self.system()}, {"role": "user", "content": user}],
                "max_tokens": max_tokens, "temperature": self.s.temperature if schema is None else 0.6, "top_p": 0.95,
                "seed": self.rng.randint(1, 10**9), "reasoning_format": "deepseek", "chat_template_kwargs": {"enable_thinking": False}}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "choice", "strict": True, "schema": schema}}
        t0 = time.time()
        r = await self.s.client.post(LLM, json=body)
        r.raise_for_status()
        out = r.json()
        dt = time.time() - t0
        timings = out.get("timings") or {}
        gen = (timings.get("prompt_ms") or 0) / 1000 + (timings.get("predicted_ms") or 0) / 1000
        self.s.calls.append({"t": self.s.now(), "player": self.pid, "purpose": purpose, "total_sec": round(dt, 2),
                             "gen_sec": round(gen, 2), "queue_sec": round(max(dt - gen, 0), 2),
                             "prompt_tokens": (out.get("usage") or {}).get("prompt_tokens"),
                             "completion_tokens": (out.get("usage") or {}).get("completion_tokens")})
        return (out["choices"][0]["message"].get("content") or "").strip()

    async def choose(self, question: str, candidates: list[str], purpose: str) -> str:
        schema = {"type": "object", "additionalProperties": False, "required": ["target"],
                  "properties": {"target": {"enum": candidates}}}
        try:
            text = await self.llm(f"{self.situation()}\n\n{question}", schema=schema, max_tokens=30, purpose=purpose)
            return json.loads(text)["target"]
        except Exception:
            return self.rng.choice(candidates)

    # ---------- behaviour ----------
    async def behave(self):
        while not self.done:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # keep the spike alive, but record it
                self.s.rejections.append({"t": self.s.now(), "player": self.pid, "agent_error": repr(error)})
                await asyncio.sleep(1)

    async def step(self):
        if self.pid not in self.alive and self.alive:
            await asyncio.sleep(1)
            return
        vote = self.action("vote")
        if vote and (self.day, self.phase_ends_at) not in self.voted:
            self.voted.add((self.day, self.phase_ends_at))
            targets = list(vote.get("valid_targets") or [])
            if targets:
                target = await self.choose("Day vote: who do you vote to execute?", targets, "vote")
                await self.ws.send(self.req("vote.cast", {"target_player_id": target}))
                self.s.log.append({"t": self.s.now(), "day": self.day, "kind": "vote", "player": self.pid, "target": target})
            return
        ability = self.action("ability")
        if ability and self.day not in self.ability_used and ability.get("uses_remaining") != 0:
            self.ability_used.add(self.day)
            targets = list(ability.get("valid_targets") or [])
            if targets:
                q = {"attack": "Night: as a werewolf, choose a player to kill tonight.",
                     "inspect": "Night: choose a player to inspect tonight.",
                     "protect": "Night: choose a player to protect tonight.",
                     "medium_inspect": "Night: choose the executed player to examine."}.get(ability.get("ability_id"), "Night: choose a target.")
                target = await self.choose(q, targets, "ability")
                await self.ws.send(self.req("ability.use", {"ability_id": ability["ability_id"], "target_player_ids": [target]}))
            return
        chat = self.action("chat")
        if self.phase == "day" and chat and chat.get("channel") == "public":
            key = (self.day, "day")
            co = self.action("co_declare")
            if co and self.day not in self.co_decided and self.spoke.get(key, 0) >= 1:
                self.co_decided.add(self.day)
                options = ["none"] + list(co.get("claimed_role_ids") or [])
                pick = await self.choose("Do you want to make a formal role claim (CO) right now? Pick 'none' to stay quiet.", options, "co")
                if pick != "none" and self.seconds_left() > 3:
                    comment = await self.llm(f"{self.situation()}\n\nYou are formally claiming to be the {pick} now. Write the one-sentence comment that goes with your claim.", max_tokens=80, purpose="co_comment")
                    await self.ws.send(self.req("co.declare", {"claimed_role_id": pick, "comment": comment[:200]}))
                    self.s.log.append({"t": self.s.now(), "day": self.day, "kind": "co", "player": self.pid, "role": pick, "comment": comment[:200]})
                    self.spoke[key] = self.spoke.get(key, 0) + 1
                    return
            if self.spoke.get(key, 0) >= 5:
                await asyncio.sleep(1)
                return
            # wait: shorter when addressed, otherwise a random human-like pause
            delay = self.rng.uniform(4, 14) if self.spoke.get(key, 0) else self.rng.uniform(1, 12)
            self.mentioned.clear()
            try:
                await asyncio.wait_for(self.mentioned.wait(), timeout=delay)
                await asyncio.sleep(self.rng.uniform(0.5, 2))
            except asyncio.TimeoutError:
                pass
            if self.phase != "day" or self.seconds_left() < 4 or self.done:
                return
            day_before = self.day
            text = None
            for _ in range(3):  # bounded resample for exact repeats only
                candidate = await self.llm(f"{self.situation()}\n\nYour next chat message as {self.pid}:", max_tokens=160)
                candidate = candidate.strip().strip('"')[:400]
                sentences = [norm(s) for s in re.split(r"(?<=[.!?。！？])\s*", candidate) if len(s) > 15]
                if candidate and norm(candidate) != (self.last_text or "") and all(self.s.sentence_counts.get(s, 0) < 2 for s in sentences):
                    text = candidate
                    break
            if text is None or self.phase != "day" or self.day != day_before or self.seconds_left() < 1:
                return
            await self.ws.send(self.req("chat.send", {"channel_id": "public", "message": text}))
            self.last_text = norm(text)
            for s in [norm(s) for s in re.split(r"(?<=[.!?。！？])\s*", text) if len(s) > 15]:
                self.s.sentence_counts[s] = self.s.sentence_counts.get(s, 0) + 1
            self.spoke[key] = self.spoke.get(key, 0) + 1
            return
        self.state_changed.clear()
        try:
            await asyncio.wait_for(self.state_changed.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--day", type=int, default=150)
    ap.add_argument("--vote", type=int, default=40)
    ap.add_argument("--night", type=int, default=40)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--out", default="spike_result.json")
    ap.add_argument("--no-teammates", action="store_true")
    args = ap.parse_args()

    content = load_content(ROOT / "content")
    preset = load_preset(ROOT / "content" / "presets" / "standard_9.yaml", content)
    preset = replace(preset, rules=replace(preset.rules, day_seconds=args.day, vote_seconds=args.vote,
                                           night_seconds=args.night, silence_after_dawn_seconds=5))
    players = tuple(PlayerConfig(f"player-{i}", f"player-{i}") for i in range(sum(preset.role_counts.values())))
    game_id = str(uuid4())
    game = GameState.create_from_preset(content, preset, players, game_id=game_id, event_sink=InMemoryEventSink(),
                                        rng=Random(args.seed), started_at=monotonic_seconds())
    registry = GameRegistry({game_id: game})
    server = WebSocketGameServer(registry, tick_interval_seconds=0.1)
    listener = await server.start("127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    tokens = registry.entry_tokens_for(game_id)
    truth = {p: game.players[p].role.id for p in game.players}
    wolves = [p for p, r in truth.items() if r == "werewolf"]
    shared = Shared(game_id, time.time(), args.lang, args.temperature)
    agents = [Agent(shared, p, tokens[p], uri, [] if args.no_teammates else [w for w in wolves if w != p] if truth[p] == "werewolf" else [], args.seed * 100 + i)
              for i, p in enumerate(sorted(game.players))]
    print("roles", truth, flush=True)
    try:
        await asyncio.wait_for(asyncio.gather(*(a.run() for a in agents), return_exceptions=True), timeout=40 * 60)
    except asyncio.TimeoutError:
        print("GLOBAL TIMEOUT", flush=True)
    # transcript reconstructed from what player-0 (or any alive viewer) saw is not enough after death:
    # use the union of public chat seen by all agents, de-duplicated by order of arrival at the longest-lived agent.
    longest = max(agents, key=lambda a: len(a.transcript))
    ended = any(a.done for a in agents)
    result = {"seed": args.seed, "roles": truth, "teammates_given": not args.no_teammates, "game_end": ended,
              "settings": vars(args), "public_chat": [t for t in longest.transcript if t[1] == "public"],
              "public_facts": longest.public_facts, "actions_log": shared.log, "calls": shared.calls,
              "rejections": shared.rejections, "wall_sec": shared.now()}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    chats = [c for c in shared.calls if c["purpose"] == "chat"]
    print("game_end", ended, "wall_sec", shared.now(), "public_chat", len(result["public_chat"]),
          "llm_calls", len(shared.calls), "rejections", len(shared.rejections), flush=True)
    if chats:
        q = sorted(c["queue_sec"] for c in chats)
        g = sorted(c["gen_sec"] for c in chats)
        print("chat gen_sec p50", g[len(g) // 2], "max", g[-1], "| queue_sec p50", q[len(q) // 2], "max", q[-1], flush=True)
    await server.close()
    await shared.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
