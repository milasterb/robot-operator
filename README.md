# Robot Operator

Voice control for a home robot, with a deterministic safety gate between the
language model and the wheels.

The robot is a Yahboom ROSMASTER X3 running ROS 2 and Nav2 in a flat. You talk
to it in plain English. A model understands what you said and picks the tools
to call. But the model never decides whether the robot may move — that answer
comes from a few hundred lines of ordinary Python with no model in them, and
nothing moves until a person says yes.

**Demo video:** _TODO_
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
  speech  ──▶  browser host  ──▶  Bedrock (Claude Haiku 4.5)
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
| Host | An MCP client, a Bedrock agent loop, and the operator panel. | `host/` |

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
| Battery | mean voltage below 10.8 V | below 11.4 V |
| Clearance | narrowest point < 1.1 × robot width | < 1.5 × robot width |
| Obstacle | something within 0.30 m right now | — |
| Stale state | telemetry older than 2 s | — |
| Localisation | the robot does not know where it is | — |
| Forbidden zone | the goal is inside one | — |
| Charger | leaving the charger on a low battery | — |

Two details that are not obvious from the table:

**Voltage is a rolling mean, not a reading.** A motor starting up drops the
pack by half a volt for a moment. Gating on the instantaneous value would make
the robot refuse to move at exactly the moment it starts moving. The raw value
is still carried in `RobotState.voltage_raw` so it can be shown and logged.

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
[`patches/Mcnamu_driver_X3.patch`](patches/) — it adds a `/gate_light`
subscriber taking `std_msgs/ColorRGBA`, and nothing else.

---

## Running it

Requires Python 3.10+ and an AWS account with Bedrock access.

```bash
git clone <this repo> && cd robot-operator
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

Bedrock configuration lives at the top of `host/agent.py`:

```python
MODEL_ID    = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
AWS_PROFILE = "alexa-ai-user"
AWS_REGION  = "us-west-2"
```

`temperature` is 0.0. Not because it makes the robot safe — it does not, which
is the whole argument above — but because a demo that behaves the same twice
is easier to trust.

### Against the real robot

On the robot, start rosbridge and Nav2, then point the server at it:

```bash
ROBOT_HOST=192.168.x.x python server.py
```

`RosRobot` reads `/voltage`, `/odom` and `/scan`, plans through
`/compute_path_to_pose`, drives through the `/navigate_to_pose` action, and
applies LIMIT by publishing `nav2_msgs/SpeedLimit` to `/speed_limit`. `halt()`
cancels the goal *and* publishes a zero `/cmd_vel`, so the wheels stop even if
the action server is slow to notice.

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

Set `robot.voltage = 10.5` to see the battery rules fire.

---

## Tests

```bash
pytest
```

Seventeen tests over the gate. The two that matter most are not about any
individual rule:

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
host/          MCP client, Bedrock agent, FastAPI server, operator panel
patches/       the change to Yahboom's driver node
tests/         gate tests
server.py      the MCP server and its eight tools
FRICTION_LOG.md
```

## Licence

MIT. See [LICENSE](LICENSE).
