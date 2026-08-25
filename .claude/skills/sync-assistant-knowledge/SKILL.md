---
name: sync-assistant-knowledge
description: Use proactively, right after making any change to RapiDBoxes' UI structure or user-facing behavior (new/renamed tabs, screens, buttons, workflows, persistence/locking rules, defaults) -- keeps back/rapidboxes/assistant/knowledge.md (PidiBot's knowledge base) in sync so it never goes stale. Also invoke on demand ("/sync-assistant-knowledge" or "update PidiBot's knowledge") to audit the whole file against current code.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
---

# Keep PidiBot's knowledge.md in sync with the real UI/behavior

`back/rapidboxes/assistant/knowledge.md` is PidiBot's entire picture of the
app -- it is loaded once as the system prompt (`_SYSTEM_PROMPT` in
`assistant/service.py`) and never re-derived from live code. If it drifts
from what the UI/API actually does, PidiBot gives wrong or incomplete
answers with total confidence, and nobody notices until a researcher
reports it. This skill exists because that already happened three times in
one session (Settings' missing General tab, Gallery's missing Folders tab,
Camera/Illumination content bleeding together) -- each one only found
because a human caught PidiBot being wrong live. Close that loop by
updating the doc in the same change that changes the behavior, not later.

## When to run this (self-check after any RapiDBoxes change)

Ask: did the change I just made touch any of these?

- A page under `front/plant-imaging-controller-faa-main/client/pages/*.tsx`,
  or a component it renders, gaining/losing/renaming a **tab, sub-view, or
  overlay** (grep the diff for new `useState` backing a union type like
  `type ...Section = "a" | "b"`, or new conditionally-rendered full-screen
  overlays -- that pattern is exactly how Settings' tabs and Gallery's
  Folders overlay are implemented).
- Any **button, toggle, or workflow step** added/removed/moved between
  tabs/screens (e.g. a field moving from Camera to Illumination, or vice
  versa).
- Backend behavior a user would notice: what persists vs. resets, what
  locks while an experiment is running, a new default value, a new
  tool/capability added to `assistant/service.py`'s `_TOOLS`, a new
  API-visible field.
- A rename of anything already documented (assistant's own name, a screen
  name, a button label).

If yes to any of these, don't wait to be asked -- update knowledge.md as
part of finishing that change, following the process below.

## Process

1. **Read the actual code you just changed** (the component/model/route
   itself, not just your own diff summary) to get the ground truth: exact
   tab names, exact field labels, exact conditions under which something
   is visible/locked/editable. Don't paraphrase from memory of what you
   intended to build -- the Camera/Illumination bug happened because prose
   assumed a field lived somewhere it visually appeared adjacent to, not
   where the component tree actually put it.

2. **Grep `knowledge.md` for existing coverage** of the changed area
   (screen name, field name, tab name) before writing anything new --
   check `wiki/index.md`-style: edit the existing section, don't create a
   duplicate one elsewhere in the file.

3. **Write the update matching the file's established conventions**
   (read a couple of existing `###` sections first if unsure):
   - One `### Section Name` per real, named UI destination (a tab, a
     screen, a distinct overlay) -- never merge two different tabs/screens
     into one section the way the old "Camera & Illumination settings, in
     detail" section did. If a field's *behavior* depends on another
     tab/screen (like exposure depending on the illumination source),
     explain the *relationship* explicitly and say plainly which tab the
     actual control lives on -- don't let proximity in the prose imply
     they're the same place.
   - When two features answer the same real-world question but live in
     different places (like Gallery download vs. Remote Sync both
     answering "how do I get my images off the device"), add an explicit
     bridging paragraph that names both -- don't rely on the model to
     infer the connection between two sections that don't reference each
     other. This was the single biggest source of "won't budge even when
     pushed" bugs found so far.
   - Add a symptom-matrix row (`| what user sees | actual cause | is it a
     bug? | what to tell them |`) for anything a real person is likely to
     report as confusing or broken, even if it's working as intended.
   - Keep prose short and plain-language -- this renders on an 800x452
     kiosk chat panel; the doc's own instruction to PidiBot ("Keep answers
     short... no wall of text") applies to how you write the doc too, not
     just how PidiBot talks.
   - Cross-reference related sections ("see X above/below") instead of
     repeating content.

4. **Self-audit against the three known failure patterns** before calling
   it done -- reread what you just wrote and check it doesn't:
   - **Tab-blind**: describe a multi-tab/multi-view screen as if it were
     flat, or omit a real tab/view that exists.
   - **Isolated-topic**: leave two features that answer the same practical
     question undocumented as being related.
   - **Misplaced content**: describe a field as living in a tab/section
     other than where its actual UI control is.

5. **Verify live before considering this done** -- the doc is worthless if
   it doesn't change PidiBot's actual answers. Ask PidiBot the exact kind
   of question a user would ask about the changed area:
   ```
   curl -s -X POST http://<device-or-simulation-host>:8000/api/assistant/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "<realistic question>", "history": [], "username": "<user>"}'
   ```
   against either a local simulation backend or (if this change is also
   being deployed) the real device, using the real e-INFRA gateway --
   mocking the LLM call proves the tool/data plumbing works, not that the
   *wording* actually produces a correct answer. Confirm the reply reflects
   the update; if it doesn't, the doc needs to be more explicit/emphatic,
   not just factually correct (models follow strong, explicit framing far
   more reliably than a technically-true aside).

6. **Run the backend test suite** if you touched anything besides
   knowledge.md (`RAPIDBOXES_SIMULATION=1 back/.venv/bin/python -m pytest
   back/tests/ -q`), then commit knowledge.md alongside the code change it
   documents (same commit if practical, otherwise immediately after) with a
   message explaining what was out of sync and why.

7. **Deploy if appropriate**, following this repo's standing safety rule:
   check `GET /api/experiments/current` on the target device first, and
   never restart `rapidboxes.service` through a genuinely active experiment
   without explicit confirmation.

## What this skill is not

Not a live code-reading tool for PidiBot itself -- that tradeoff (latency,
cost, and raw source being unreliable to interpret correctly even for an
LLM) was considered and rejected in favor of a curated, human-reviewed doc.
This skill is the discipline that keeps that curated doc from rotting: pay
the update cost once, at change time, instead of on every user question
forever.
