# Friction log

Build, Ship, Shape: Amazon Developer Hackathon 2026 — Alexa+ track.

Project: a voice operator for a home robot. A self-hosted MCP server exposes
the robot through tools; a deterministic safety gate sits between the model
and the hardware; a browser host stands in for Alexa+ because the toolkit is
not available to participants.

Entries are in the order I hit them. Times are Europe/Prague.

---

## 1. The Alexa+ Developer Console is advertised but not open

**2026-09-21**

**What I did.** Signed up at developer.amazon.com, finished the Sole
Proprietorship details and identity check, and followed the dashboard link
labelled "Alexa+ Developer Console — Create and manage your add-on."

**What I expected.** A console where I could register an MCP add-on, since
the hackathon's Alexa+ track was already running and the documentation
describes the console as the place add-ons are created.

**What happened.** The page says "Coming Soon."

**How I worked around it.** Went back to the docs and found that the MCP
onboarding path runs through the Alexa AI CLI instead, so the console is not
strictly required.

**Suggestion.** Either hide the link or label it with its availability. A
developer who lands there during a hackathon has no way to tell whether the
console is unreleased, region-locked, or broken for their account, and the
obvious conclusion — that they did something wrong — costs time.

---

## 2. A new AWS account takes 24 hours, and the setup guide does not say so

**2026-09-21**

**What I did.** Started "Set up your development environment," which requires
an AWS account in order to assume a role and reach the CLI package.

**What I expected.** A prerequisite list that tells me, before I begin, what
each step will cost me in wall-clock time.

**What happened.** Account creation finished, but AWS held activation for up
to 24 hours. Nothing further could be attempted until it cleared.

**How I worked around it.** Built the parts of the project that do not touch
AWS — the MCP server, the safety gate — and came back later.

**Suggestion.** Put a note at the top of the setup page: creating a new AWS
account can take up to 24 hours to activate, so start there first. For a
hackathon with a fixed deadline, a day of latency is material, and it is the
kind of thing that is obvious to the team writing the guide and invisible to
everyone else.

---

## 3. The setup guide walks you into a role you are not allowed to assume

**2026-09-25**

**What I did.** Followed the CLI installation steps exactly: created an IAM
user, attached an inline policy allowing `sts:AssumeRole` on
`arn:aws:iam::372468808636:role/AddOn3PDeveloperToolsRead`, configured a
profile with that role and `source_profile`, and ran
`aws sts get-caller-identity --profile alexa-ai`.

**What I expected.** Assumed-role credentials, then a CodeArtifact login and
`npm install` of the CLI, as the guide describes.

**What happened.**

```
AccessDenied: User: arn:aws:iam::164482057582:user/alexa-ai-tools is not
authorized to perform: sts:AssumeRole on resource:
arn:aws:iam::372468808636:role/AddOn3PDeveloperToolsRead
```

My half of the trust relationship was correct. The role's own trust policy,
in Amazon's account, does not include mine — and there is no way for me to
see that, let alone change it.

**How I worked around it.** I could not. I stopped, rather than debug a
configuration that was already right.

**Suggestion.** The guide's phrasing ("the AWS account you provided to your
Alexa Solutions Architect") hints that accounts are allow-listed, but the
steps read as though anyone can follow them. Say plainly, in the
prerequisites, that the toolkit is limited to approved accounts and how one
gets approved. Better still, have the role return a message that says the
account is not enrolled, rather than a generic authorization failure — a
developer who sees `AccessDenied` will assume they misconfigured something
and will keep trying.

---

## 4. Participants cannot get toolkit or simulator access at all

**2026-09-25**

**What I did.** Asked in the hackathon's Discussions whether participants can
get toolkit access or an official Alexa+ simulator, so I could test account
linking and MCP App rendering on the real surface.

**What happened.** The organisers answered clearly: no, there is no way for
participants to get access to the toolkit or the simulator, and a self-built
simulator or other front end is a fine way to demo.

**How I worked around it.** Built my own host: a browser panel with speech
in and out, talking to a backend that is a real MCP client over Streamable
HTTP. The visual layer is a standard MCP App, so it should render on a real
device unchanged if access ever opens up.

**Suggestion.** This answer is the single most useful thing I learned all
week, and it was in a forum thread rather than on the track page. Put it in
the rules: state up front that Alexa+ tooling is not available to
participants and that a self-built host is the expected path. It would have
saved me the two days that entries 1 to 3 cost, and I doubt I am the only
one who spent them.

---

## 5. Amazon's MCP examples use an API that pip no longer installs

**2026-09-21**

**What I did.** `pip install "mcp[cli]"`, then wrote a server following the
shape used in the Alexa+ MCP examples: `from mcp.server.fastmcp import FastMCP`.

**What happened.**

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x,
where FastMCP was renamed to MCPServer
```

In v2 the class is `MCPServer`, and the transport arguments
(`stateless_http`, `json_response`, host, port) moved off the constructor
onto `run()`.

**How I worked around it.** Rewrote against v2. The SDK's own error message
is excellent — it names the rename and links the migration guide — which is
why this cost ten minutes instead of an hour.

**Suggestion.** Pin or version-label the SDK in the Alexa+ examples. Anyone
installing today gets v2 and the sample code fails on its first import.

---

## 6. Bedrock is gated behind a second, undocumented verification

**2026-09-26, 16:14**

**What I did.** With the AWS account finally active, added
`bedrock:InvokeModel` and friends to my IAM user, confirmed
`ListFoundationModels` returned the Claude models, and called `converse`.

**What happened.**

```
AccessDeniedException: Your account is currently being verified.
Verification normally takes less than 2 hours.
```

So: 24 hours to activate the account, then up to 2 more before a model can
be invoked, on top of the day lost to entry 3.

**How I worked around it.** Wrote the agent and the panel against an
interface that does not care which provider answers, and left the call
unexercised until verification cleared.

**Suggestion.** Surface this on the Bedrock console before the first call,
not as an exception after it. `ListFoundationModels` succeeding while
`Converse` fails on verification is a confusing pair of signals — the account
looks ready until the moment it matters.

---

## 7. `bedrock:Converse` is not an IAM action

**2026-09-26, 16:12**

**What I did.** Wrote an IAM policy granting `bedrock:Converse` and
`bedrock:ConverseStream`, matching the API operation names.

**What happened.** The policy editor rejected both: "the action
bedrock:Converse does not exist." The Converse API authorises through
`bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`.

**How I worked around it.** Dropped the two nonexistent actions.

**Suggestion.** The IAM editor caught it, which is good. What would be better
is for the Converse API documentation to state which IAM actions authorise
it, next to the operation itself. Guessing from the operation name is the
obvious move and it is wrong.

---

## 8. ROS and Python tooling fight over PYTHONPATH

**2026-09-26, 16:00**

**What I did.** Ran `pytest` in a virtualenv on a workstation that also
sources ROS 2 in `.bashrc`.

**What happened.** pytest crashed before collecting a single test: ROS's
`launch_testing` entry point was on `PYTHONPATH`, pytest auto-loaded it as a
plugin, and it failed importing `osrf_pycommon`. Nothing to do with my code.

**How I worked around it.** Stopped sourcing ROS globally and load it
explicitly in the shells that need it. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`
works as a one-off.

**Suggestion.** Not Amazon's problem, but worth recording for anyone building
an MCP server that talks to robots: keep the ROS environment off your
development shell. The MCP server does not need ROS at all when it reaches
the robot over rosbridge.

---

## 9. Writing tools for a voice agent means forbidding helpfulness

**2026-09-26**

**What I found.** Tool descriptions written the way one writes API docs
produce the wrong behaviour in a voice agent. Two things had to be stated
outright in the system prompt, because the model's default is to be useful:

- that it must not look for a way around a refusal. When the safety gate says
  no, a helpful assistant naturally proposes an alternative route. In a voice
  interface that sounds like the robot negotiating with you about its own
  safety limits.
- that `propose_move` moves nothing. Without it the model treats proposing
  and doing as one step and skips the confirmation.

**Suggestion.** If Amazon publishes guidance for Alexa+ add-on authors, a
section on tools that carry physical consequences would be worth having.
Every instinct that makes an assistant pleasant to talk to works against you
when the tool call moves a machine.

---

## 10. A stateless MCP server cannot volunteer anything

**2026-09-26**

**What I found.** The design calls for the robot to offer to drive to the
charger when the battery gets low. A stateless Streamable HTTP server has no
way to initiate that: it can only answer. The offer has to ride along with
the next response, or the client has to poll.

For a voice surface this matters more than it would elsewhere. Something a
person needs to be told — the battery is low, the robot stopped on its own —
has no path to their ears until they happen to ask a question.

**Suggestion.** Document the intended pattern for add-ons that have something
to say between turns, even if the answer is "attach it to the next response."
Right now it reads like an omission rather than a decision.

---

## What went well

Not everything was friction, and the parts that worked are worth naming.

- **Streamable HTTP in stateless mode is genuinely simple.** One POST, one
  JSON response. I wrote a working MCP client in about seventy lines, with no
  SDK, and could debug the whole protocol with `curl`. Being able to see the
  wire format by hand made every other problem easier to isolate.
- **Protocol version negotiation is unambiguous.** The `initialize` response
  states the agreed version, so "am I on a version this track accepts?" is one
  request, not an investigation.
- **The tool-result contract fits voice well.** Returning a sentence meant to
  be read aloud, rather than a JSON blob, kept the model from paraphrasing
  safety-critical text. The gate writes the words; the model relays them.
- **The MCP Python SDK's migration error** (entry 5) is the best error message
  I saw all project: it named the change, gave the new symbol, and linked the
  guide.
