# Robot Operator

Voice control for a home robot, with a deterministic safety gate between the
language model and the wheels.

The robot is a Yahboom ROSMASTER X3 running ROS 2 and Nav2 in a flat. You talk
to it in plain English. A model understands what you said and picks the tools
to call. But the model never decides whether the robot may move — that answer
comes from a few hundred lines of ordinary Python with no model in them, and
nothing moves until a person says yes.

**Demo video:** https://youtu.be/OiNN0rRtVUc
**Devpost:** _TODO_

---

## Why it is built this way

An agent that can drive a machine around a home is a different proposition
from an agent that can write an email. A bad email wastes a minute. A robot
that drives into a staircase, or into a person, does not.

The usual answer is to make the model more careful: a stern system prompt,
tools with warnings in their descriptions, a fine-tune. That improves the
average case and does nothing for the one that matters, because it is still
the model deciding — and a model's decision is a sample from a distribution.
You can shift the distribution. You cannot put a floor under it.

So this project puts the floor somewhere else. Between the tools and the
hardware sits a **gate**: a pure function from the robot's measured state and
a proposed move to one of three verdicts.

```
ALLOW   go
LIMIT   go at reduced speed, and say why
STOP    do not go, and say why
```

The gate has no model in it, no randomness, and no network calls. The same
input gives the same verdict every time — there is a test that asserts
exactly that. It can be read in one sitting, argued with, and unit-tested,
which is not true of a prompt.

The model's job is narrowed to what models are good at: understanding a
sentence, choosing a tool, and explaining a refusal in words a person
understands. It is not trusted with the decision, so it does not have to be
trustworthy for the system to be safe.

### Nothing moves without a person

Every move is two calls, not one.

`propose_move` plans a route, runs the gate, and — if the verdict is not STOP —
issues a **single-use token** that expires in 30 seconds. It changes nothing.
Its docstring says so, and so does the system prompt, because the model will
read both.

`confirm_move` takes that token and is the only thing in the system that makes
the robot move. It redeems the token with a `pop`, so replaying it fails. And
it **runs the gate again** before driving: the world may have changed in the
seconds between the question and the answer, and a verdict earned a moment ago
is not a permission that keeps.

`stop` is the exception. It is never gated, never confirmed, and it discards
every outstanding token.

---

## How the pieces fit

```
  speech  ──▶  browser host  ──▶  language model (Claude or a local one)
                    │                      │
                    │                 tool calls
                    ▼                      ▼
              panel / map  ◀──────  MCP client
                                           │  Streamable HTTP
                                           ▼
                                    MCP server  (server.py)
                                           │
                                    ┌──────┴──────┐
                                    ▼             ▼
                                  gate         adapter
                            (deterministic)   ┌────┴────┐
                                              ▼         ▼
                                         FakeRobot   RosRobot
                                                        │ rosbridge
                                                        ▼
                                                 ROS 2 · Nav2 · X3
```

| Layer | What it is | Where |
|---|---|---|
| Gate | The safety rules. Pure, deterministic, no I/O. | `gate/` |
| Adapter | Everything the tools need from a robot, behind one `Protocol`. | `robot/` |
| MCP server | Eight tools over Streamable HTTP, protocol `2025-11-25`. | `server.py` |
| Host | An MCP client, an agent loop with a switchable model, and the operator panel. | `host/` |

The gate never sees the adapter and the adapter never sees the gate. They meet
only through two frozen dataclasses, `RobotState` and `MoveProposal`. That is
why the same gate runs unchanged against a robot in memory and against the
real one.

### Why there is a hand-built host

The Alexa+ developer toolkit is not available to hackathon participants — the
organisers confirmed this in the Devpost discussions, and suggested a
self-built front end instead. So the host here is a real MCP client written
from scratch (`host/mcp_client.py`, about seventy lines: `initialize`,
`tools/list`, `tools/call`, with the protocol version enforced). It speaks to
the server exactly as any other client would.

This turned out to be worth more than the toolkit would have been. Writing the
client is what surfaced most of the entries in [`FRICTION_LOG.md`](FRICTION_LOG.md).

---

## The rules

All of them live in `gate/rules.py`. Each is a function with the same
signature, and they are collected in one list, so adding a rule is adding a
function. `Level` is an `IntEnum`, so combining verdicts is `max()` — the
strictest one wins, and every reason that fired is reported, not just the
first.

| Rule | STOP when | LIMIT when |
|---|---|---|
| `check_state_age` | telemetry is older than 2 s | — |
| `check_localization` | the robot does not know where it is | — |
| `check_battery` | mean voltage below 10.8 V | below 11.4 V |
| `check_plan` | the planner returned no route | — |
| `check_zones` | the goal is inside a forbidden zone | — |
| `check_clearance` | narrowest point < 1.1 × robot width | < 1.5 × robot width |
| `check_obstacle` | something within 0.30 m right now | — |

Three details that are not obvious from the table:

**Voltage is a rolling mean, not a reading.** A motor starting up drops the
pack by half a volt for a moment. Gating on the instantaneous value would make
the robot refuse to move at exactly the moment it starts moving. The raw value
is still carried in `RobotState.voltage_raw` so it can be shown and logged.

**A flat battery does not strand the robot.** Below 10.8 V every trip is
refused — except one. A move whose goal is the charger — the place marked
`"charger": true` in the site, with the **Charger** tool in the editor — is allowed, at reduced
speed, with the reason said out loud. The alternative is a gate that is
correct and useless: a robot that has decided it is too low on power to move,
and therefore cannot reach the thing that would fix that. The exception lives
inside `check_battery` rather than in a rule of its own, because it is a
property of the battery rule and nothing else.
A map with no charger marked has no exception: on a flat battery every trip
is refused, and the robot says why.

**Verdicts have hysteresis.** Without it, a robot sitting at 10.79 V flips
between STOP and LIMIT with every reading and the light bar strobes. The
threshold to recover is 0.2 V higher than the threshold to trip. This is the
sort of thing that is obvious on hardware and invisible in a simulator.

`Gate.evaluate()` changes nothing — it is safe to call to ask a question.
`Gate.commit()` is what advances the hysteresis state and writes the log.
There is a test that asserts `evaluate` has no side effects.

### Forbidden zones

A zone is a polygon on the map with a name. The one in this flat is the
corridor, and the reason it is forbidden is measured rather than imagined: the
corridor is 3.5 m of two parallel walls, the LiDAR sees the same scan
everywhere along it, and AMCL cannot tell how far down it the robot has
driven. The robot genuinely does get lost in there.

That is the kind of limit worth encoding. It is specific to this machine in
this building, it is the sort of thing an operator knows and a model cannot,
and the refusal it produces is a technical fact rather than an apology.

---

## The light bar

The X3 has an addressable LED strip across its back. The panel shows the
verdict to whoever is holding the laptop; the light bar shows it to everyone
else in the room, which is the case that actually matters — an operator with
both hands busy is not looking at a screen.

| State | Colour | Meaning |
|---|---|---|
| idle | dim blue | powered, listening, nothing being decided |
| waiting | green, breathing | a move is proposed and waiting on a human |
| allow | green | cleared and moving |
| limit | orange | moving under a restriction |
| stop | red | refused, or stopped |

Only `waiting` animates, and it breathes rather than blinks, because a
blinking machine reads as a fault and this is an ordinary pause. Everything
else is published once and left alone: the LED strip shares a serial link with
the motors and the IMU, so this code stays quiet unless it has something to
say.

Driving the strip needs a small change to Yahboom's own driver node, which
does not expose the LEDs on a topic. The change is in
[`patches/`](patches/) — it adds a `/gate_light` subscriber taking
`std_msgs/ColorRGBA`, and nothing else.

---

## Running it

Requires Python 3.10+ and one language model: either an Anthropic API key
(Claude, needs internet) or [Ollama](https://ollama.com) running locally
(no internet, no account).

```bash
git clone https://github.com/milasterb/robot-operator && cd robot-operator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Terminal 1 — the MCP server.** Defaults to the in-memory robot, so no
hardware is needed:

```bash
python server.py            # 127.0.0.1:8000
```

**Terminal 2 — the host and the panel:**

```bash
uvicorn host.server:app --port 8080
```

Open <http://localhost:8080> in Chrome. Hold the button and speak, or type.
Speech uses the Web Speech API, which is Chrome-only; the text box works
everywhere.

### Choosing the model

The model is picked by environment variables, read from the shell or from a
`.env` file in the repository root. Start from the example:

```bash
cp .env.example .env        # then edit it
```

```bash
LLM_BACKEND=anthropic       # or: ollama
ANTHROPIC_API_KEY=sk-ant-…  # only for anthropic
ANTHROPIC_MODEL=claude-haiku-5-5
OLLAMA_MODEL=qwen3:8b       # only for ollama: `ollama pull qwen3:8b` first
```

A variable set in the shell wins over `.env`, so one run can use the other
model without editing anything:

```bash
LLM_BACKEND=ollama uvicorn host.server:app --port 8080
```

The panel shows which model is answering in its top bar, and
`python check_llm.py` asks the configured model one question with no robot
attached — the quickest way to see that a key or a pulled model works.

Both backends live in `host/llm.py` behind the same three methods; the agent
loop in `host/agent.py` does not know which one it is talking to, and the gate
does not know there is a model at all. With Ollama the temperature is 0.0 —
not because it makes the robot safe (it does not, which is the whole argument
above) but because a demo that behaves the same twice is easier to trust.

### Language: English or Czech

The **CZ / EN** switch in the panel's header changes the language of the
robot and of every open panel at once. `ROBOT_LANG=cs` in `.env` only sets
which one it starts in (default `en`); a switch lasts until the next restart.

The robot server owns the language, because the robot's sentences are
written there; the host asks it before every utterance and uses the matching
system prompt. Switching also starts a new conversation — a small model given
half a conversation in each language tends to stay in the first — and
withdraws a trip that was offered but not yet confirmed, so nobody confirms a
sentence they read before the switch. **New chat** in the conversation header
forgets the conversation without changing the language.

Every sentence about safety is a fixed template in `i18n.py`: the model reads
the gate's reasons out, it does not translate them, so a refusal says exactly
the same thing whichever model is answering. Czech uses a decimal comma and
quotes place names („kuchyň“); spoken names are matched without diacritics,
so *kuchyne* finds *kuchyň*. The speech recogniser and the voice follow the
switch (`cs-CZ`), which needs a Czech voice installed in the browser or the OS.

The language changes only words. `tests/test_i18n.py` checks that the gate
reaches the same verdicts in both, and that every message has the same blanks
in both.

### Against the real robot

On the robot, start the laser bring-up, Nav2, rosbridge and the plan relay
(`onboard/plan_relay.py`, run inside the robot's container), then point the
server at it:

```bash
ROBOT_HOST=192.168.x.x SITE=obyvak python server.py
```

`RosRobot` reads `/voltage`, `/odom`, `/amcl_pose` and `/scan`. Routes come
from Nav2's planner through the relay, because rosbridge cannot reach a ROS 2
action. Goals go out on `/goal_pose`. LIMIT is applied by writing the DWB
controller's speed parameters, since Nav2 Foxy has no `/speed_limit` topic.
`halt()` publishes a zero `/cmd_vel`, replaces the goal with the robot's own
pose, and publishes zero again.

### Your own room: sites

Everything about a room — its map, its named places and its forbidden zones —
lives in a folder under `sites/`, not in the code:

```
sites/obyvak/
  map.yaml, map.pgm   the occupancy grid, exactly as map_saver wrote it
  site.json           places and zones, in metres in the map frame
```

`SITE=obyvak` (in the shell or in `.env`) picks one. Without it the in-memory
robot gets `sites/demo`, the flat below, and the real robot gets `obyvak`.

The panel edits the current site. **Edit map** opens the tools: add a place
with a click, draw a forbidden zone corner by corner, drag either to move it,
scroll to zoom. **Save** sends the drawing to the server, which refuses
anything it cannot make sense of — a zone that crosses itself, two places
with the same name, a corner off the map — and only then hands the new zones
to the gate. A trip proposed before the change has to be proposed again.
Every save is appended to the site's `changes.log`.

**New map…** turns a map from the robot into a new site: select the `.yaml`
and `.pgm` that `map_saver_cli` wrote, give it a name, and draw its places
and zones. With the in-memory robot the panel can also switch between sites.
With a real robot it cannot: the zones only mean something in the frame of
the map Nav2 is localising against, so the site is chosen when the server
starts.

With a real robot, **Place at robot** adds a place exactly where the robot
stands: drive it there with the keyboard, click, name it. The coordinates
are right by construction, and the spot is one the robot has reached.

The zones are coordinates, so they only mean something on the map they were
drawn on. When the server connects to a real robot it asks Nav2's
`map_server` for the map it is localising against and compares it with the
site's: resolution, size, origin and the cells themselves. If they differ,
the gate refuses every trip and the panel says why. If the robot's map
cannot be read at all, the panel says the zones are unverified but trips are
not blocked. The comparison is repeated every minute and on **Check again**,
so restarting Nav2 on another map is caught too.

None of this is an MCP tool. The model cannot list, change or even see the
editing routes, because it must never be able to move the fences it is kept
inside.

### The in-memory robot

`FakeRobot` is laid out so that each destination exercises a different path
through the gate, because a gate that has only ever been seen saying yes has
not been seen working:

| Say | Verdict | Why |
|---|---|---|
| "drive to the kitchen" | ALLOW | 0.55 m doorway |
| "drive to the living room" | ALLOW | 0.80 m doorway |
| "drive to the bathroom" | **LIMIT** | 0.26 m doorway — half speed |
| "drive to the front door" | **STOP** | 0.18 m gap, too narrow to fit |
| "go to the bedroom" | **STOP** | inside the corridor |

Set `robot.voltage = 10.5` to see the battery rules fire, including the
exception for the charger.

---

## Tests

```bash
pytest
```

Seventeen tests over the gate and twenty-two over sites — what a valid map,
place and zone is, that a broken map never becomes a site, and that a
robot on a different map is refused. The two that
matter most are not about any individual rule:

- `test_evaluate_changes_nothing` — asking the gate a question must not alter
  it, or the second question gets a different answer.
- `test_the_same_input_always_gives_the_same_verdict` — the property the whole
  design rests on.

The gate has no ROS dependency, so the tests run anywhere. If `pytest` fails
on an `osrf_pycommon` import, a ROS setup script has leaked onto your
`PYTHONPATH`; open a shell that does not source one.

---

## What this does not do

Worth saying plainly, since the point of the project is not overclaiming.

- **The gate is as good as its inputs.** It reasons about clearance from the
  closest laser return rather than by sampling the costmap. That errs towards
  refusing, which is the right direction, but it is a coarse number.
- **Localisation is detected, not solved.** The gate knows when AMCL has lost
  the robot and refuses to move it. Getting it un-lost is Nav2's problem.
- **No model is involved in the gate, and no gate is involved in the model.**
  A model that never calls `propose_move` at all cannot be stopped by this
  design — the tools are the only route to the hardware, which is why
  `confirm_move` is the single door and why it re-checks.
- **The flat's map is the weak link.** The gate's zone and the real `PLACES`
  coordinates are only as trustworthy as the SLAM run behind them.

---

## Repository

```
gate/          the rules, the verdict types, the gate itself
robot/         adapter Protocol, FakeRobot, RosRobot, tokens, light bar
host/          MCP client, agent loop, model backends, FastAPI server, operator panel
layout/        sites: loading maps, checking places and zones, the change log
sites/         one folder per room: map, places, forbidden zones
patches/       the change to Yahboom's driver node
tests/         gate, site and language tests
i18n.py        every sentence, in English and Czech
server.py      the MCP server and its eight tools
FRICTION_LOG.md
PRODUCT_FEEDBACK.md
```

## Licence

MIT. See [LICENSE](LICENSE).
