# Product feedback

Feedback on every tool, API and SDK used to build Robot Operator, in the order
they were hit. Each entry covers what it was used for, what worked, what needs
work, what onboarding felt like, and whether I would use it again.

Context for weighing this: I am a secondary-school student building on a
Yahboom ROSMASTER X3 in a flat, on my own, in evenings and at weekends. Most
of what follows is about the gap between documentation written for a team with
an existing AWS footprint and a person starting from nothing.

---

## Alexa+ developer toolkit

**Used for:** nothing, in the end. The project is in the Alexa+ track and the
plan was to drive it through the official toolkit and simulator.

**What happened:** the Alexa+ Developer Console shows "Coming Soon". I then
tried the Alexa AI CLI path, which asks to assume an Amazon-owned IAM role,
and got `AccessDenied` on `sts:AssumeRole`. I spent roughly a full evening
assuming this was a misconfiguration on my side — checking my IAM user, my
trust policy, my region — before asking in the Devpost discussions, where the
organisers confirmed that there is no way for participants to get access to
the toolkit or the simulator, and that a self-built front end is a fine
substitute.

**What needs work:** this is the single highest-value fix available. One line
on the track page — "the toolkit is not available to participants; build your
own front end" — would have saved me an evening, and I doubt I am the only
one. An `AccessDenied` on an Amazon-owned role is indistinguishable from a
mistake the developer made, so everyone who hits it will debug their own setup
first. If the toolkit is gated, say so where people read the requirements, not
only in a discussion thread they have to think to search.

**Onboarding:** the console existing but being empty is worse than it not
existing, because it reads as "you have arrived but configured something
wrong".

**Would I use it again:** yes, if it ships. The track itself is a good idea and
the MCP route turned out to be the more interesting one anyway.

---

## Model Context Protocol (spec 2025-11-25, Streamable HTTP)

**Used for:** the robot is exposed as eight MCP tools over Streamable HTTP.
This is the core of the project.

**What worked:** the spec is readable end to end in an afternoon, which is rare
and worth saying. Because it is small, I was able to write my own client by
hand — `initialize`, `tools/list`, `tools/call`, about seventy lines — once the
toolkit turned out to be unavailable. A protocol you can reimplement from the
document in an evening is a protocol that will get adopted. Stateless
Streamable HTTP with JSON responses was exactly right for a server whose state
lives in the robot rather than in the session.

**What needs work:** tool descriptions are the only channel for conveying a
constraint to the model, and the spec has nothing to say about them. My
`propose_move` tool must not be treated as a move, and the only way I can
express that is English prose in a docstring, repeated in the system prompt
and hoped for. An optional, structured notion of a tool that requires
confirmation — or an advertised pairing between a proposing tool and a
committing one — would turn a convention into something a client could
enforce. As it stands, every project that needs human-in-the-loop invents its
own token scheme, as I did.

**Onboarding:** good. The versioning is explicit and the handshake fails loudly
on a mismatch, which is the correct behaviour and made my own client easy to
get right.

**Would I use it again:** yes, and I expect to for projects outside this
hackathon.

---

## MCP Python SDK (v2)

**Used for:** the server side — `MCPServer`, the `@mcp.tool()` decorator, the
Streamable HTTP transport.

**What worked:** the decorator API is close to ideal. A typed function with a
docstring becomes a tool with a schema and a description, and there is nothing
to keep in sync by hand. I added tools without thinking about the protocol once.

**What needs work:** the v1 → v2 rename is not discoverable from the error.
Following a tutorial written against v1 gives:

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

`FastMCP` is now `MCPServer`, and the transport arguments moved from the
constructor to `run()`. Neither of those is in the exception, and a search for
the error returns v1-era material, so the trail leads backwards. A deprecation
shim that raises `FastMCP has been renamed to MCPServer in SDK v2` would cost
three lines and save everyone arriving from a tutorial — which, for a protocol
this young, is most people.

**Onboarding:** quick once past the rename. Under ten minutes from install to a
tool responding.

**Would I use it again:** yes.

---

## AWS Bedrock (Converse API, Claude Haiku 4.5)

**Used for:** the agent loop in the host. The model receives the user's
sentence and the tool list, chooses tools, and puts the gate's verdict into
words. `temperature` is 0.0.

**What worked:** the Converse API's tool-use loop maps onto MCP almost one to
one — tool definitions in, `toolUse` blocks out, `toolResult` back. I did not
have to write a translation layer, which is a real compliment to the API
design. Haiku 4.5 is more than capable for this: in testing it consistently
called `list_places` before `propose_move` without being told to, and it never
once tried to work around a refusal.

**What needs work:** four separate failures between an account with Bedrock
"enabled" and a first successful call, each with an error that pointed
somewhere other than the fix.

1. `AccessDeniedException` immediately after enabling model access. The real
   cause was that the account was still being verified. Nothing in the message
   said "wait"; I assumed a policy problem and rewrote my IAM policy twice.
2. An invocation on the bare model ID failed with a message about inference
   profiles. The fix is the `us.` prefix — `us.anthropic.claude-haiku-...`. The
   console shows the bare ID, so the thing you copy is not the thing that
   works.
3. I wrote `bedrock:Converse` in my IAM policy, since that is the API I am
   calling. It is not a valid action; the required action is
   `bedrock:InvokeModel`. The policy editor accepted the invalid action without
   complaint and the failure arrived later, at runtime.
4. Then `AccessDenied` on `aws-marketplace`, fixed by adding
   `aws-marketplace:ViewSubscriptions` and `aws-marketplace:Subscribe` —
   permissions I would not have guessed were involved in calling a model.

Any one of these is a minor annoyance. In sequence, each one looking like the
same class of problem as the last, they cost an evening. The common thread is
that the error names the symptom and never the remedy. A single
"getting started, from zero" IAM policy published alongside the Converse docs —
the complete set of actions needed to call one model, marketplace permissions
included — would have removed all four.

**Onboarding:** the hardest part of the project, and it is not close. The model
and the API are good; getting permission to reach them is not.

**Would I use it again:** yes. The setup is a one-time cost and the API itself
is well designed.

---

## AWS account setup, IAM, CLI, Budgets

**Used for:** an IAM user for the host, MFA, a zero-spend budget, and the CLI
with a named profile.

**What worked:** named profiles are clean — `AWS_PROFILE` in one place and
nothing else in the code knows about credentials. The zero-spend budget is
genuinely reassuring for a student account and took two minutes.

**What needs work:** `SignatureDoesNotMatch` is a bad error. It is what you get
from a mistyped secret key, a stale key, and a clock skew, and the message
distinguishes none of them. Also: the IAM policy editor validating action names
against the real action list at save time, rather than silently accepting
`bedrock:Converse`, would catch a whole category of mistakes hours earlier.

**Onboarding:** fine for the account itself. IAM is where the time goes.

**Would I use it again:** yes.

---

## rosbridge_suite and roslibpy

**Used for:** the bridge between the Python MCP server and ROS 2 on the robot.
Subscriptions to `/voltage`, `/odom` and `/scan`; the `/navigate_to_pose`
action; the `/compute_path_to_pose` service; publishing to `/cmd_vel`,
`/speed_limit` and my own `/gate_light`.

**What worked:** this is the piece that made the architecture possible. The MCP
server runs on my laptop in a normal Python environment with no ROS on the
path, which means the safety gate's tests run anywhere. Topics, services and
actions all work over the websocket, including custom message types, with no
code generation step.

**What needs work:** `throttle_rate` and `queue_length` on a subscription are
not optional for `/scan` at 10 Hz and are easy to miss — without them the
websocket backs up. The deeper problem is that ROS 2 and `pytest` fight over
`PYTHONPATH`: sourcing a ROS setup script in `.bashrc` makes an unrelated test
run die on `ModuleNotFoundError: osrf_pycommon`, which gives no hint that the
cause is a shell profile.

**Would I use it again:** yes, without hesitation.

---

## Nav2

**Used for:** path planning (`ComputePathToPose`), navigation
(`NavigateToPose`), speed restriction (`nav2_msgs/SpeedLimit` on
`/speed_limit`), and AMCL for localisation.

**What worked:** being able to ask for a path *without* driving it is what made
the two-phase design possible at all. `propose_move` plans and judges and
changes nothing; `confirm_move` drives. Without a plan-only service I would
have had to simulate the planner or move first and apologise later.
`SpeedLimit` as a percentage is exactly the right shape for a LIMIT verdict.

**What needs work:** AMCL's failure mode is silence. In a 3.5 m corridor of two
parallel walls the particle filter oscillates between hypotheses several metres
apart, and the only way I found to see it was watching the `map → odom`
transform jump. There is a covariance published, and I intend to gate on it,
but nothing surfaces "I am not confident" as a first-class signal. For a robot
that is about to be told to drive somewhere, that is the single most important
thing it could say.

**Would I use it again:** yes.

---

## gmapping / SLAM

**Used for:** building the map of the flat.

**What worked:** when it works, it works.

**What needs work:** this is the part of the project I have failed at most. Four
mapping runs produced unusable maps, and the lesson each time arrived only
after the run finished: the robot must not be picked up mid-run; rotating too
fast loses the scan match; and a flat with repeating geometry needs a
deliberate loop closure. All three are invisible while mapping and obvious
afterwards. A live quality signal during the run — even a crude "scan match
confidence is poor right now" — would have turned four wasted runs into one.

**Would I use it again:** yes, with far more respect for it.

---

## Yahboom ROSMASTER X3 driver stack

**Used for:** the robot itself — motors, IMU, battery voltage, and the LED bar.

**What worked:** the hardware is solid and the ROS 2 bring-up works out of the
box.

**What needs work:** the LED bar is reachable only from inside the driver node;
there is no topic for it. Signalling the gate's verdict to the room meant
patching Yahboom's own `Mcnamu_driver_X3.py` to add a `/gate_light` subscriber
taking `std_msgs/ColorRGBA` — about fifteen lines, and now a local modification
I have to carry. The patch is in the repo. Separately, two processes opening
the serial port silently yields `0.0` for voltage and IMU rather than an error,
which is a dangerous default for a value a safety system reads: my gate would
have happily treated 0.0 V as a flat battery and refused everything, which is
at least fail-safe, but only by luck.

**Would I use it again:** yes.

---

## Web Speech API (Chrome)

**Used for:** speech input in the operator panel.

**What worked:** a working voice interface in about fifteen lines, with no key
and no service.

**What needs work:** Chrome only, so the panel's voice path is tied to one
browser. The text box is there for everyone else.

**Would I use it again:** for a demo, yes. For anything shipped, no.

---

# Feature requests

**Critical — publish toolkit availability on the track page.**
State plainly, in the Alexa+ track requirements, whether the developer toolkit
and simulator are available to participants. The current state sends people to
debug an `AccessDenied` that cannot be fixed.

**Important — a zero-to-first-call IAM policy for Bedrock.**
One copy-pasteable policy document, published with the Converse getting-started
guide, containing every action required to invoke one model from a fresh
account — `bedrock:InvokeModel` and the `aws-marketplace` permissions included.

**Important — validate action names in the IAM policy editor.**
`bedrock:Converse` is not a valid action and was accepted silently. Failing at
save time instead of at runtime would catch this class of error immediately.

**Important — say "account verification pending" when that is the cause.**
`AccessDeniedException` on a newly enabled model is indistinguishable from a
policy error, and the two have completely different remedies.

**Nice-to-have — show the inference-profile ID in the console.**
The console displays the bare model ID; invocation requires the `us.` prefix.
Showing the ID that actually works, or offering both with a label, removes a
guaranteed first-attempt failure.

**Nice-to-have — a deprecation shim in the MCP Python SDK.**
`mcp.server.fastmcp` raising "renamed to MCPServer in SDK v2" rather than
`ModuleNotFoundError` would redirect everyone arriving from a v1 tutorial.

**Nice-to-have — a confirmation-required hint in the MCP spec.**
An optional, structured way for a tool to declare that it proposes rather than
acts, and to name the tool that commits it. Human-in-the-loop is common enough
in agent systems touching the physical world that it should not be reinvented
per project.
