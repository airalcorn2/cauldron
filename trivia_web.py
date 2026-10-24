"""Web layer for the horror-trivia game -- two separate interfaces.

An aiohttp HTTP+WebSocket server (aiohttp is already a project dependency,
used today by voice_generator.py for ElevenLabs calls -- its aiohttp.web
module is a full async server, so that part needed no new pip package;
QR code generation below does add one: `qrcode`, pure Python, no Pillow
needed since it renders straight to SVG).

Two static pages, both driven by the same WebSocket broadcasts, each
just rendering them differently:
  - **group** (`/`) -- meant for a shared screen (a TV). Opens on a title
    screen with one QR code per team; once the round starts, switches to
    a big scoreboard + the current category/clue/points + the outcome of
    each attempt. Never answers anything itself.
  - **team** (`/team/<id>`) -- meant for one team's phones. Reached by
    scanning that team's QR code (or typing the URL), so there's no
    manual "join" step; shows the scoreboard and a free-text answer box,
    normally enabled only when it's this team's turn -- except during a
    tied-score sudden-death tiebreaker, where both teams' boxes open at
    once for a single numeric guess.

trivia_game.py owns all the actual rules; this module only knows
"broadcast this to everyone" and "give me the next answer from team N"
-- see Server below.

Run directly to poke at the pages/WebSocket without a real game loop
behind it (every broadcast just echoes back what you'd expect):

    .venv/bin/python trivia_web.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import qrcode
import qrcode.image.svg
from aiohttp import web

if TYPE_CHECKING:
    from trivia_data import Clue
    from trivia_game import Phase, TeamId, TriviaState

STATIC_DIR = Path(__file__).parent / "trivia_static"
GROUP_PAGE_PATH = STATIC_DIR / "group.html"
TEAM_PAGE_PATH = STATIC_DIR / "team.html"


@dataclass
class Server:
    """Handed back by start(); trivia_game.run() drives a round through
    this -- broadcast_*() push to every connected page (group and team
    alike; each just renders what it cares about), wait_for_answer()
    blocks until the given team's phone (any of them) submits one.
    """

    runner: web.AppRunner
    team_names: dict["TeamId", str]
    game_title: str
    sockets: set[web.WebSocketResponse] = field(default_factory=set)
    _answers: dict["TeamId", "asyncio.Queue[str]"] = field(
        default_factory=lambda: {1: asyncio.Queue(), 2: asyncio.Queue()}
    )
    # Which team has already locked in an answer for the *current* active
    # clue -- a teammate's second phone submitting after that is just
    # ignored, rather than queued and mistaken for the next clue's answer.
    _answered: set["TeamId"] = field(default_factory=set)
    # The most recent message of each type -- replayed to a freshly
    # connected/reconnected socket (see _send_snapshot()) so a reloaded
    # team or group page catches up to the live round instantly instead of
    # sitting on "waiting for the next question" until the next broadcast.
    _last_state: dict | None = None
    _last_prompt: dict | None = None
    _last_timer: dict | None = None
    _last_result: dict | None = None
    _last_end: dict | None = None
    _last_tiebreaker: dict | None = None
    _last_tiebreaker_result: dict | None = None
    _last_submitted: dict | None = None
    _last_finale: dict | None = None

    async def _broadcast(self, message: dict) -> None:
        payload = json.dumps(message)
        dead = []
        for ws in self.sockets:
            try:
                await ws.send_str(payload)
            except ConnectionResetError:
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    async def broadcast_state(
        self, state: "TriviaState", *, answering_team: "TeamId | None" = None
    ) -> None:
        """``answering_team`` is whoever may actually submit right now --
        state.current_team during a primary attempt, but the OTHER team
        during a steal -- so the group screen's scoreboard highlight tracks
        the real answerer instead of freezing on whoever started the round.
        """
        message = {
            "type": "state",
            "scores": state.team_scores,
            "current_team": state.current_team,
            "answering_team": answering_team,
            "phase": state.phase,
        }
        self._last_state = message
        await self._broadcast(message)

    async def broadcast_prompt(
        self,
        team: "TeamId",
        clue: "Clue",
        phase: "Phase",
        *,
        question_number: int,
        total_questions: int,
    ) -> None:
        """Announce whose turn it is and what the clue was, now that the
        witch has finished reading it aloud -- also resets the
        already-answered guard for the new clue."""
        self._answered = set()
        for q in self._answers.values():
            while not q.empty():
                q.get_nowait()
        message = {
            "type": "prompt",
            "team": team,
            "phase": phase,
            "category": clue.category,
            "clue": clue.clue,
            "points": clue.points,
            "round": clue.round,
            "question_number": question_number,
            "total_questions": total_questions,
        }
        self._last_prompt = message
        # All stale -- they were for the previous clue.
        self._last_timer = None
        self._last_result = None
        self._last_submitted = None
        await self._broadcast(message)

    async def broadcast_submitted(self, team: "TeamId", *, stop_timer: bool) -> None:
        """Announce that ``team`` has locked in an answer, the instant it
        lands -- lets the group screen show a "locked in" indicator and
        stop the countdown immediately, rather than leaving it visibly
        ticking down through however long judging takes.

        ``stop_timer`` is False during a sudden-death tiebreaker (see
        trivia_game._play_tiebreaker()): both teams answer the same
        clock there, so one team submitting doesn't mean the other is
        done thinking -- only the per-team "locked in" indicator applies,
        not the shared countdown.
        """
        message = {"type": "submitted", "team": team, "stop_timer": stop_timer}
        self._last_submitted = message
        await self._broadcast(message)

    async def broadcast_timer(self, seconds: float) -> None:
        """Announce that the answer clock starts now, for the group
        screen's countdown -- a Unix deadline (``time.time() + seconds``),
        not just ``seconds`` by itself, so a page that only sees this via
        _send_snapshot() (joining or reloading mid-wait) still computes
        the correct remaining time rather than restarting the clock from
        the top."""
        message = {"type": "timer", "deadline": time.time() + seconds}
        self._last_timer = message
        await self._broadcast(message)

    async def broadcast_result(
        self, team: "TeamId", correct: bool, phase: "Phase", banter: str
    ) -> None:
        """Announce the outcome of one attempt -- plus the judging witch's
        in-character banter line (see trivia_game.judge_answer()) -- before
        the next clue (or steal prompt) goes out, rather than jumping
        straight to the next question with no feedback in between."""
        message = {
            "type": "result",
            "team": team,
            "correct": correct,
            "phase": phase,
            "banter": banter,
        }
        self._last_result = message
        self._last_submitted = None
        await self._broadcast(message)

    async def broadcast_end(self, state: "TriviaState", winner: "TeamId") -> None:
        """Announce the round is over and who won, once the clue pool is
        exhausted."""
        message = {"type": "end", "scores": state.team_scores, "winner": winner}
        self._last_end = message
        await self._broadcast(message)

    async def broadcast_finale(self, lines: dict[str, str]) -> None:
        """Reveal the three witches' closing lines (witch name -> text),
        once generated -- its own broadcast fired after broadcast_end(),
        so "Game Over!" appears immediately while
        trivia_game.generate_finale()'s Gemini call and synthesis are
        still running, rather than making the whole finale wait before
        the screens update at all. Plain strings, not a domain type, so
        this module stays free of a dependency on trivia_game.py's
        Witch/FinaleLine types -- same reasoning as
        broadcast_tiebreaker_result()'s plain float/str arguments."""
        message = {"type": "finale", "lines": lines}
        self._last_finale = message
        await self._broadcast(message)

    async def broadcast_tiebreaker(self, question: str) -> None:
        """Announce a sudden-death numeric question -- unlike
        broadcast_prompt(), BOTH teams may answer this one, not just
        whoever's turn it normally is. Resets the already-answered guard
        the same way, and clears the last real clue's prompt/timer/result
        so a page reconnecting mid-tiebreaker renders this instead of
        replaying stale state from the clue that ended the main round."""
        self._answered = set()
        for q in self._answers.values():
            while not q.empty():
                q.get_nowait()
        message = {"type": "tiebreaker", "question": question}
        self._last_tiebreaker = message
        self._last_prompt = None
        self._last_timer = None
        self._last_result = None
        self._last_tiebreaker_result = None
        self._last_submitted = None
        await self._broadcast(message)

    async def broadcast_tiebreaker_result(
        self,
        *,
        guess_1: float | None,
        guess_2: float | None,
        answer: float,
        source: str,
        winner: "TeamId",
    ) -> None:
        """Reveal both guesses, the real (sourced) answer, and who won,
        once the sudden-death tiebreaker resolves."""
        message = {
            "type": "tiebreaker_result",
            "guess_1": guess_1,
            "guess_2": guess_2,
            "answer": answer,
            "source": source,
            "winner": winner,
        }
        self._last_tiebreaker_result = message
        self._last_submitted = None
        await self._broadcast(message)

    async def _send_snapshot(self, ws: web.WebSocketResponse) -> None:
        """Replay the latest cached broadcast of each type to one socket --
        called right after it connects, so a freshly loaded or reloaded
        page lands on the live round's current state instead of an empty
        "waiting" screen until the next broadcast happens to fire."""
        for message in (
            self._last_state,
            self._last_prompt,
            self._last_timer,
            self._last_submitted,
            self._last_result,
            self._last_tiebreaker,
            self._last_tiebreaker_result,
            self._last_end,
            self._last_finale,
        ):
            if message is not None:
                await ws.send_str(json.dumps(message))

    async def wait_for_answer(self, team: "TeamId") -> str:
        return await self._answers[team].get()

    async def stop(self) -> None:
        for ws in list(self.sockets):
            await ws.close()
        self.sockets.clear()
        await self.runner.cleanup()

    def _handle_submission(self, team: "TeamId", text: str) -> bool:
        """Returns whether this submission was accepted (first for the
        active clue) or ignored (a teammate's already answered)."""
        if team in self._answered:
            return False
        self._answered.add(team)
        self._answers[team].put_nowait(text)
        return True


async def _group_page(request: web.Request) -> web.Response:
    return web.Response(text=GROUP_PAGE_PATH.read_text(), content_type="text/html")


async def _team_page(request: web.Request) -> web.Response:
    return web.Response(text=TEAM_PAGE_PATH.read_text(), content_type="text/html")


def _make_teams_handler(server: Server):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(
            {"title": server.game_title, "teams": server.team_names}
        )

    return handler


def _make_qr_handler():
    async def handler(request: web.Request) -> web.Response:
        team_id = request.match_info["team_id"]
        if team_id not in ("1", "2"):
            raise web.HTTPNotFound()
        join_url = f"http://{request.host}/team/{team_id}"
        image = qrcode.make(join_url, image_factory=qrcode.image.svg.SvgPathImage)
        return web.Response(body=image.to_string(), content_type="image/svg+xml")

    return handler


def _make_ws_handler(server: Server):
    async def handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        server.sockets.add(ws)
        await server._send_snapshot(ws)
        try:
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "answer":
                    team = data.get("team")
                    text = data.get("text", "")
                    if (team in (1, 2)) and isinstance(text, str) and text.strip():
                        accepted = server._handle_submission(team, text.strip())
                        await ws.send_str(
                            json.dumps({"type": "ack", "accepted": accepted})
                        )
        finally:
            server.sockets.discard(ws)
        return ws

    return handler


async def start(
    *,
    host: str = "0.0.0.0",
    port: int = 8080,
    team_names: dict["TeamId", str] | None = None,
    game_title: str = "Trivia",
) -> Server:
    """Start the HTTP+WebSocket server and return a Server to drive it."""
    app = web.Application()
    server = Server(
        runner=web.AppRunner(app),
        team_names=team_names or {1: "Team 1", 2: "Team 2"},
        game_title=game_title,
    )

    app.router.add_get("/", _group_page)
    app.router.add_get("/team/{team_id}", _team_page)
    app.router.add_get("/teams", _make_teams_handler(server))
    app.router.add_get("/qr/{team_id}.svg", _make_qr_handler())
    app.router.add_get("/ws", _make_ws_handler(server))

    # Routes must be registered before setup() -- it freezes the router.
    await server.runner.setup()
    site = web.TCPSite(server.runner, host, port)
    await site.start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the trivia web server on its own, with no real "
        "game loop behind it -- for poking at the pages/WebSocket directly."
    )
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    async def _run() -> None:
        server = await start(
            port=args.port,
            team_names={1: "Team Tarot", 2: "Team Broomstick"},
            game_title="The Cauldron of Knowledge",
        )
        print(f"Group screen: http://0.0.0.0:{args.port}/")
        print(f"Team pages:   http://0.0.0.0:{args.port}/team/1  and  /team/2")
        print("Ctrl-C to stop.")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await server.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
