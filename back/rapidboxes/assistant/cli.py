"""SSH-reachable terminal client for the QA assistant.

Talks to the same /api/assistant/chat endpoint the touchscreen uses. Never
starts an experiment on its own: a resolved proposal is pretty-printed via
format_config_knobs() and requires an explicit y/N confirmation before this
CLI ever calls the real start API -- same hard, do-not-revisit requirement
as the web UI (see AssistantChat.tsx and AssistantService's docstring).

Usage, from an SSH session on the Pi (or any machine that can reach it):
    rapidboxes-assistant [--host http://127.0.0.1:8000] [--user ivan]
or:
    python -m rapidboxes.assistant.cli [--host ...] [--user ...]
"""
from __future__ import annotations

import argparse
import getpass
from typing import List

import httpx

from ..models import SavedExperimentConfig
from .service import format_config_knobs


def _prompt(text: str) -> str:
    try:
        return input(text)
    except EOFError:
        return ""


def _print_boxed(text: str) -> None:
    lines = text.splitlines()
    width = max((len(line) for line in lines), default=0)
    print("+" + "-" * (width + 2) + "+")
    for line in lines:
        print(f"| {line.ljust(width)} |")
    print("+" + "-" * (width + 2) + "+")


def _build_start_payload(config: dict, username: str, experiment_name: str) -> dict:
    """Maps a resolved proposal's SavedExperimentConfig (a protocol-agnostic
    snapshot, same shape used to replay a run from the web UI's Import) onto
    the narrower TropismConfig/GrowthConfig shape POST /api/experiments
    expects -- same field names, just a protocol-specific subset, no
    translation needed. Camera/LED/IR settings are applied separately (see
    _handle_proposal), same two-step split the web UI's "load a past
    experiment" flow uses."""
    protocol = config["protocol"]
    base = {"protocol": protocol, "experimentName": experiment_name, "username": username}
    if protocol == "tropism":
        base.update(
            darkPhaseEnabled=config["darkPhaseEnabled"],
            darkPhaseHours=config["darkPhaseHours"],
            lateralIlluminationHours=config["lateralIlluminationHours"],
            spectra=config["spectra"],
            intervalMinutes=config["intervalMinutes"],
            intensity=config["intensity"],
        )
    else:
        base.update(
            dayLengthHours=config["dayLengthHours"],
            experimentLengthDays=config["experimentLengthDays"],
            spectra=config["spectra"],
            dayIntensity=config["dayIntensity"],
            intervalMinutes=config["intervalMinutes"],
        )
    return base


class AssistantCli:
    def __init__(self, base_url: str, username: str):
        self.username = username
        self.client = httpx.Client(base_url=base_url, timeout=120.0)
        self.history: List[dict] = []

    def close(self) -> None:
        self.client.close()

    def run(self) -> None:
        print(f"RaPiD-boxes QA assistant (as '{self.username}'). 'quit' or Ctrl-D to exit.\n")
        while True:
            message = _prompt("you> ").strip()
            if not message:
                continue
            if message.lower() in ("quit", "exit"):
                break
            self._turn(message)

    def _turn(self, message: str) -> None:
        try:
            res = self.client.post(
                "/api/assistant/chat",
                json={"message": message, "history": self.history, "username": self.username},
            )
        except httpx.HTTPError as exc:
            print(f"assistant> [connection error: {exc}]\n")
            return

        if res.status_code == 409:
            print("assistant> [chat is unavailable while an experiment is running]\n")
            return
        if res.status_code == 503:
            detail = res.json().get("detail", "assistant unavailable")
            print(f"assistant> [{detail}]\n")
            return
        if res.status_code != 200:
            print(f"assistant> [error {res.status_code}: {res.text}]\n")
            return

        body = res.json()
        print(f"assistant> {body['reply']}\n")
        self.history.append({"role": "user", "content": message})
        self.history.append({"role": "assistant", "content": body["reply"]})

        if body.get("proposal"):
            self._handle_proposal(body["proposal"])

    def _handle_proposal(self, proposal: dict) -> None:
        saved = SavedExperimentConfig.model_validate(proposal["config"])
        print(f"--- Proposed experiment: {proposal['experimentId']} ({proposal['sourceUsername']}) ---")
        _print_boxed(format_config_knobs(saved))

        if _prompt("Start this experiment now? [y/N] ").strip().lower() != "y":
            print("Not started.\n")
            return

        name = _prompt("Experiment name [assistant-replay]: ").strip() or "assistant-replay"
        try:
            current = self.client.get("/api/settings")
            current.raise_for_status()
            settings = current.json()
            settings["camera"] = saved.camera.model_dump()
            settings["leds"] = saved.leds.model_dump()
            settings["ir"] = saved.ir.model_dump()
            settings["photoIlluminationSource"] = saved.photoIlluminationSource
            put = self.client.put("/api/settings", json=settings)
            put.raise_for_status()

            payload = _build_start_payload(proposal["config"], self.username, name)
            start = self.client.post("/api/experiments", json=payload)
            start.raise_for_status()
            print(f"Started: {start.json()}\n")
        except httpx.HTTPError as exc:
            print(f"[could not start: {exc}]\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default=None, help="username to chat/start as (defaults to $USER)")
    args = parser.parse_args()

    cli = AssistantCli(args.host, args.user or getpass.getuser())
    try:
        cli.run()
    except KeyboardInterrupt:
        pass
    finally:
        cli.close()


if __name__ == "__main__":
    main()
