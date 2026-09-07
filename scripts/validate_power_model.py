#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "aiohttp>=3.8.0",
#     "async-timeout>=4.0.0",
# ]
# ///
"""Check the power model against the cloud's own hourly consumption.

The power sensor computes ``load_watt x percent``. Two attributes could supply
that percent: ``outputPercentDisplay`` (what the integration currently uses) and
``floorSetpointPwm`` (fetched on every poll but never parsed). If the wrong one
is being integrated, the resulting energy is wrong -- which is what a comparison
against the cloud's hourly watt-hours showed.

This script samples *both* candidates over one or more complete clock hours,
converts each into a predicted watt-hours for the hour, and scores them against
the actual watt-hours the consumption endpoint reports for that same hour. The
candidate that matches is the one the sensor should be using.

Dependencies are declared inline (PEP 723), so uv fetches them into a throwaway
environment -- nothing to install, and Home Assistant is never imported.

Usage:
    export SCHLUTER_USERNAME=you@example.com
    export SCHLUTER_PASSWORD=...
    uv run scripts/validate_power_model.py --probe          # one-shot, ~5 requests
    uv run scripts/validate_power_model.py                  # sample 1 hour, then score

The shebang also carries `uv run --script`, so ./scripts/validate_power_model.py
works directly. Plain `python3 scripts/validate_power_model.py` still works too,
provided aiohttp is already available.

Run it while the floor is actually heating. An idle hour reads zero on every
candidate and discriminates nothing.

Two cautions:

* The cloud enforces a daily request cap. Sampling every 60s costs ~60 requests
  per hour on top of whatever Home Assistant is already spending. Use
  --interval 120 to halve that; the duty-cycle average barely suffers.
* The cloud also caps concurrent sessions, and this script logs in as you. If it
  fails with a session-limit error, the Home Assistant integration is probably
  holding the session -- stop the integration (or just accept that one of the two
  will be logged out) and retry.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import types
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from getpass import getpass
from typing import Any

import aiohttp

# Load api.py without executing the integration's __init__.py, which imports
# Home Assistant -- this script needs to run with nothing but aiohttp installed.
# Registering a synthetic parent package lets api.py's `from .const import ...`
# resolve while the real __init__.py stays untouched.
_INTEGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components",
    "schluterditraheat",
)
_pkg = types.ModuleType("_schluter")
_pkg.__path__ = [_INTEGRATION]
sys.modules["_schluter"] = _pkg

_api = importlib.import_module("_schluter.api")
SchluterApi = _api.SchluterApi
SchluterApiError = _api.SchluterApiError

# Attributes sampled from the device on every poll.
CANDIDATES = ["outputPercentDisplay", "floorSetpointPwm"]


def model_percent_as_duty(row: dict[str, float | None]) -> float | None:
    """The integration's current model: power = load_watt x percent.

    Treats outputPercentDisplay as a duty cycle to be averaged.
    """
    return row.get("outputPercentDisplay")


def model_on_off(row: dict[str, float | None]) -> float | None:
    """Element is either fully on or fully off; percent only says *which*.

    Sampling shows outputPercentDisplay sitting at 0 for most of a ~15 minute PWM
    cycle and jumping to a quantized 20/40/60 for the minutes the cable actually
    conducts. If that non-zero window IS the conducting window, then instantaneous
    power is the full connected load, not a fraction of it -- a resistive cable
    switches, it does not modulate. Averaging this over an hour gives
    load_watt x (fraction of time on), which is the physically meaningful figure.
    """
    percent = row.get("outputPercentDisplay")
    if percent is None:
        return None
    return 100.0 if percent > 0 else 0.0


def model_floor_pwm(row: dict[str, float | None]) -> float | None:
    """Kept only to keep proving it is dead -- it reads 0 even at full demand."""
    return row.get("floorSetpointPwm")


# Competing models for "what percentage of full load was drawn on average".
MODELS: dict[str, Any] = {
    "percent-as-duty": model_percent_as_duty,
    "on/off full load": model_on_off,
    "floorSetpointPwm": model_floor_pwm,
}


async def consumption_with_reauth(api: SchluterApi, device_id: int) -> dict[str, Any]:
    """Fetch consumption, re-authenticating once if the session has expired.

    A sampling run plus the wait for the cloud's lagging buckets can outlive a
    session, and losing an hour of samples to USRSESSEXP would be absurd.
    """
    try:
        return await api.get_consumption_history(device_id, "hourly")
    except SchluterApiError as err:
        if "SESSEXP" not in str(err):
            raise
        print("  session expired -- re-authenticating")
        await api.authenticate()
        return await api.get_consumption_history(device_id, "hourly")


def coerce_percent(value: Any) -> float | None:
    """Pull a percentage out of an attribute whose shape we don't know.

    The API is inconsistent: some attributes are bare numbers, others wrap the
    value in ``{"value": n}`` or ``{"percent": n}``. Return None when there is
    no number to be had, so a missing attribute is distinguishable from a real
    zero -- that distinction is the whole point of this exercise.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("percent", "value"):
            inner = value.get(key)
            if isinstance(inner, (int, float)):
                return float(inner)
    return None


def load_watt(raw: dict[str, Any]) -> float:
    """Sum the connected load across both outputs, as the integration does."""
    total = 0.0
    for key in ("loadWattOutput1", "loadWattOutput2"):
        watts = coerce_percent(raw.get(key))
        if watts:
            total += watts
    return total


async def probe(api: SchluterApi, device_id: int) -> dict[str, Any]:
    """Dump one raw attribute payload plus recent consumption. ~5 requests."""
    raw = await api.get_device_attributes(device_id)
    print("\n=== Raw attribute payload ===")
    print(json.dumps(raw, indent=2, sort_keys=True))

    watts = load_watt(raw)
    print("\n=== Candidate percentages, right now ===")
    for name in CANDIDATES:
        print(f"  {name:<24} raw={raw.get(name)!r:<28} -> {coerce_percent(raw.get(name))}")
    print(f"  {'load_watt (sum)':<24} {watts} W")

    consumption = await api.get_consumption_history(device_id, "hourly")

    # The response's own metadata. We parse `period` as watt-hours purely because
    # the API labels the unit "watts" and we assumed it meant Wh-per-bucket. That
    # assumption is exactly what is in doubt, so show every field it ships.
    print("\n=== Consumption response: top-level fields ===")
    for key, value in consumption.items():
        if key == "history":
            print(f"  {key:<16} (list of {len(value)} buckets)")
        else:
            print(f"  {key:<16} {value!r}")
    print("\n  first 3 history buckets, verbatim:")
    for item in consumption.get("history", [])[:3]:
        print(f"    {json.dumps(item, sort_keys=True)}")

    points = api.parse_consumption_history(consumption)
    print(f"\n=== Cloud hourly consumption (last {min(12, len(points))} of {len(points)}) ===")
    print(f"  {'hour (UTC)':<22}{'period':>9}{'as Wh':>8}{'implied duty %':>17}")
    for start, kwh in points[-12:]:
        wh = kwh * 1000
        duty = f"{100 * wh / watts:>16.1f}" if watts else "  (no load_watt)"
        print(f"  {start.strftime('%Y-%m-%d %H:%M'):<22}{wh:>9.0f}{wh:>8.1f}{duty}")

    # Cross-check the granularities. If 24 hourly buckets sum to the matching
    # daily bucket, the numbers are at least internally consistent and we are
    # misreading what they MEAN. If they don't, we are misreading their
    # STRUCTURE -- a very different bug. Two extra requests.
    print("\n=== Granularity cross-check ===")
    by_day: dict[Any, float] = defaultdict(float)
    for start, kwh in points:
        by_day[start.date()] += kwh * 1000

    for gran in ("daily", "monthly"):
        try:
            other = await api.get_consumption_history(device_id, gran)
        except SchluterApiError as err:
            print(f"  {gran}: unavailable ({err})")
            continue
        other_points = api.parse_consumption_history(other)
        print(f"\n  {gran} (last 4 of {len(other_points)}):")
        for start, kwh in other_points[-4:]:
            wh = kwh * 1000
            note = ""
            if gran == "daily" and start.date() in by_day:
                summed = by_day[start.date()]
                ratio = wh / summed if summed else float("nan")
                note = f"   <- hourly buckets for this day sum to {summed:.0f} (x{ratio:.2f})"
            print(f"    {start.strftime('%Y-%m-%d %H:%M'):<22}{wh:>10.0f}{note}")

    if watts:
        print(
            "\nRead this three ways:\n"
            "  * If a candidate percentage tracks 'implied duty', that's the one to use.\n"
            "  * If the daily bucket equals the sum of that day's hourly buckets, the\n"
            "    figures are self-consistent and we're misreading their units/meaning.\n"
            "  * If 'implied duty' stays high while the thermostat is plainly idle,\n"
            "    'period' is not heating energy at all and the energy import is wrong.\n"
        )
    return raw


async def sample(
    api: SchluterApi, device_id: int, interval: int, hours: int
) -> dict[datetime, list[dict[str, float | None]]]:
    """Poll both candidates until `hours` complete clock hours have been covered.

    Samples are bucketed by UTC hour. Only hours we covered end to end are
    returned -- a partially sampled hour would understate whichever candidate
    happened to be low during the sampled part.
    """
    buckets: dict[datetime, list[dict[str, float | None]]] = defaultdict(list)
    now = datetime.now(timezone.utc)
    # Start of the next full hour: the first hour we can cover completely.
    first_full = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    end = first_full + timedelta(hours=hours)

    print(
        f"\nSampling every {interval}s until {end:%Y-%m-%d %H:%M} UTC "
        f"({hours} complete hour(s) starting {first_full:%H:%M}).\n"
        f"Roughly {int((end - now).total_seconds() // interval)} requests. Ctrl-C to stop early;\n"
        f"whatever complete hours exist will still be scored.\n"
    )

    try:
        while datetime.now(timezone.utc) < end:
            stamp = datetime.now(timezone.utc)
            try:
                raw = await api.get_device_attributes(device_id)
            except SchluterApiError as err:
                print(f"  {stamp:%H:%M:%S}  request failed: {err}")
                await asyncio.sleep(interval)
                continue

            row: dict[str, float | None] = {
                name: coerce_percent(raw.get(name)) for name in CANDIDATES
            }
            row["load_watt"] = load_watt(raw)
            buckets[stamp.replace(minute=0, second=0, microsecond=0)].append(row)

            shown = "  ".join(f"{n}={row[n]}" for n in CANDIDATES)
            print(f"  {stamp:%H:%M:%S}  {shown}  load={row['load_watt']}")
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("\nInterrupted -- scoring the complete hours collected so far.")

    # Keep only hours that are fully in the past and well covered.
    expected = 3600 / interval
    complete = {
        hour: rows
        for hour, rows in buckets.items()
        if hour >= first_full
        and hour + timedelta(hours=1) <= datetime.now(timezone.utc)
        and len(rows) >= expected * 0.9
    }
    return complete


def mean(values: list[float | None]) -> float | None:
    """Mean of the samples that actually carried a number."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


async def compare_devices(api: SchluterApi, devices: dict[int, dict[str, Any]]) -> None:
    """Check whether the consumption endpoint is actually scoped to one device.

    It echoes back the deviceId it was asked for, but the figures it returns are
    far too large for a single small cable and carry on regardless of whether
    that cable is heating. If every device on the account returns the *same*
    history, the endpoint is really reporting account- or location-wide totals
    and this integration must not attribute them to individual thermostats.
    """
    print("\n=== Is consumption really per-device? ===")
    if len(devices) < 2:
        print(
            "  Only one thermostat on this account, so this cannot be tested here.\n"
            "  The check matters on multi-thermostat accounts: identical histories\n"
            "  across devices would mean the endpoint is not device-scoped."
        )
        return

    histories: dict[int, list[tuple[datetime, float]]] = {}
    for device_id, info in devices.items():
        try:
            raw = await api.get_consumption_history(device_id, "hourly")
        except SchluterApiError as err:
            print(f"  device {device_id}: unavailable ({err})")
            continue
        histories[device_id] = api.parse_consumption_history(raw)

    if len(histories) < 2:
        print("  Fewer than two histories came back -- cannot compare.")
        return

    ids = sorted(histories)
    print(f"\n  {'hour (UTC)':<18}" + "".join(f"{'dev ' + str(i):>12}" for i in ids))
    hours = sorted({start for pts in histories.values() for start, _ in pts})[-8:]
    for hour in hours:
        line = f"  {hour:%Y-%m-%d %H:%M}"
        for device_id in ids:
            wh = dict(histories[device_id]).get(hour)
            line += f"{wh * 1000:>12.0f}" if wh is not None else f"{'-':>12}"
        print(line)

    first = histories[ids[0]]
    identical = all(histories[i] == first for i in ids[1:])
    print()
    if identical:
        print(
            "  IDENTICAL across every device. The endpoint is NOT device-scoped --\n"
            "  it returns account/location totals and merely echoes the deviceId.\n"
            "  Importing this per-thermostat would multiply the house's energy by\n"
            "  the number of thermostats. The energy import cannot ship as written."
        )
    else:
        print(
            "  Histories differ between devices, so the endpoint IS device-scoped.\n"
            "  The figures must then be explained some other way -- they remain far\n"
            "  too large for the measured load."
        )


def save_samples(hourly: dict[datetime, list[dict[str, float | None]]], path: str) -> None:
    """Persist samples so a scoring failure never costs another hour of sampling."""
    payload = {hour.isoformat(): rows for hour, rows in hourly.items()}
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2)
    print(f"\nSamples saved to {path}")
    print(f"Score them later without re-sampling:  --score-from {path}")


def load_samples(path: str) -> dict[datetime, list[dict[str, float | None]]]:
    """Reload samples written by save_samples."""
    with open(path) as handle:
        payload = json.load(handle)
    return {datetime.fromisoformat(hour): rows for hour, rows in payload.items()}


async def score(
    api: SchluterApi,
    device_id: int,
    hourly: dict[datetime, list[dict[str, float | None]]],
    wait_minutes: int = 150,
) -> None:
    """Compare each candidate's predicted watt-hours against the cloud's actual.

    The consumption endpoint lags: its newest bucket has been observed roughly
    two hours behind the wall clock. So rather than sleeping a fixed interval and
    hoping, poll until every sampled hour actually has a bucket, then score.
    """
    if not hourly:
        print("\nNo complete hours were sampled -- nothing to score.")
        return

    actual: dict[datetime, float] = {}
    deadline = datetime.now(timezone.utc) + timedelta(minutes=wait_minutes)

    while True:
        consumption = await consumption_with_reauth(api, device_id)
        actual = {
            start: kwh * 1000 for start, kwh in api.parse_consumption_history(consumption)
        }
        missing = sorted(h for h in hourly if h not in actual)
        if not missing:
            break

        newest = max(actual) if actual else None
        if datetime.now(timezone.utc) >= deadline:
            print(
                f"\nGave up waiting for {len(missing)} bucket(s): "
                f"{', '.join(f'{h:%H:%M}' for h in missing)}.\n"
                f"The cloud's newest bucket is {newest:%Y-%m-%d %H:%M} UTC. Re-score later with\n"
                f"--score-from once it catches up; the samples are already saved."
            )
            break

        print(
            f"\nCloud has not published {', '.join(f'{h:%H:%M}' for h in missing)} yet"
            + (f" (newest bucket: {newest:%H:%M} UTC)" if newest else "")
            + ". It lags ~2h; checking again in 10 minutes."
        )
        await asyncio.sleep(600)

    print("\n=== Predicted vs actual watt-hours ===")
    header = f"{'hour (UTC)':<18}{'n':>4}{'actual Wh':>11}{'implied %':>11}"
    for name in MODELS:
        header += f"{name + ' %':>20}{'pred Wh':>10}{'err':>8}"
    print(header)

    totals: dict[str, list[float]] = {name: [] for name in MODELS}
    unscored: list[datetime] = []

    for hour in sorted(hourly):
        rows = hourly[hour]
        if hour not in actual:
            unscored.append(hour)
            print(f"{hour:%m-%d %H:%M}  no cloud bucket published for this hour yet")
            continue

        watts = mean([r["load_watt"] for r in rows]) or 0.0
        actual_wh = actual[hour]
        implied = 100 * actual_wh / watts if watts else float("nan")

        line = f"{hour:%m-%d %H:%M}  {len(rows):>4}{actual_wh:>11.1f}{implied:>11.1f}"
        for name, model in MODELS.items():
            pct = mean([model(r) for r in rows])
            if pct is None or not watts:
                line += f"{'n/a':>20}{'-':>10}{'-':>8}"
                continue
            pred_wh = watts * pct / 100
            err = 100 * (pred_wh - actual_wh) / actual_wh if actual_wh else float("nan")
            totals[name].append(abs(err))
            line += f"{pct:>20.1f}{pred_wh:>10.1f}{err:>7.0f}%"
        print(line)

    print("\n=== Verdict ===")

    # A missing cloud bucket is not the same as missing samples. Say which it is:
    # reporting "no usable data" when the floor ran flat out for an hour is a lie.
    if unscored and not any(totals.values()):
        for hour in unscored:
            rows = hourly[hour]
            summary = ", ".join(
                f"{name}={mean([model(r) for r in rows])}" for name, model in MODELS.items()
            )
            watts = mean([r["load_watt"] for r in rows]) or 0.0
            print(
                f"  {hour:%m-%d %H:%M}  sampled fine ({len(rows)} samples: {summary}, "
                f"load={watts:.0f} W)\n"
                f"            but the cloud has not published this hour, so it cannot be scored."
            )
        print(
            "\n  Nothing is wrong with the samples -- the consumption endpoint simply\n"
            "  lags. Re-run with --score-from once it catches up."
        )
        return

    ranked = []
    for name in MODELS:
        if totals[name]:
            avg = sum(totals[name]) / len(totals[name])
            ranked.append((avg, name))
            print(f"  {name:<24} mean absolute error {avg:>6.1f}%")
        else:
            print(f"  {name:<24} never carried a usable number")

    if not ranked:
        print("\n  Neither candidate produced a number. Was the floor idle the whole time?")
        return

    ranked.sort()
    best_err, best = ranked[0]
    print(f"\n  Closest match: {best} ({best_err:.1f}% mean error)")

    if best_err >= 15:
        print(
            "  -> No model is convincing. Compare 'implied %' against each model's\n"
            "     column by hand; something else is going on."
        )
    elif best == "on/off full load":
        print(
            "  -> The cable switches rather than modulates: instantaneous power is the\n"
            "     full connected load whenever outputPercentDisplay > 0, and 0 otherwise.\n"
            "     The sensor must NOT multiply load_watt by the percentage -- doing so\n"
            "     under-reports, because the attribute already reads 0 for the off phase\n"
            "     of each PWM cycle."
        )
    elif best == "percent-as-duty":
        print(
            "  -> The integration's current model is correct: load_watt x percent, with\n"
            "     the percentage treated as an averageable duty cycle."
        )


async def main() -> int:
    # A sampling run prints a line a minute for over an hour. Line-buffer stdout
    # so that progress is still visible when the output is piped or redirected.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--device-id", type=int, help="default: the first thermostat found")
    parser.add_argument("--interval", type=int, default=60, help="seconds between samples (default 60)")
    parser.add_argument("--hours", type=int, default=1, help="complete hours to sample (default 1)")
    parser.add_argument("--probe", action="store_true", help="dump one payload and exit; no sampling")
    parser.add_argument(
        "--samples",
        default="power_samples.json",
        help="where to save samples (default power_samples.json)",
    )
    parser.add_argument(
        "--score-from",
        metavar="FILE",
        help="score samples saved by an earlier run instead of sampling again",
    )
    args = parser.parse_args()

    username = os.environ.get("SCHLUTER_USERNAME") or input("Schluter username: ")
    password = os.environ.get("SCHLUTER_PASSWORD") or getpass("Schluter password: ")

    async with aiohttp.ClientSession() as session:
        api = SchluterApi(session, username, password)
        try:
            await api.authenticate()
        except SchluterApiError as err:
            print(f"Login failed: {err}", file=sys.stderr)
            print(
                "A session-limit error means Home Assistant (or the app) is holding the\n"
                "session -- stop the integration and retry.",
                file=sys.stderr,
            )
            return 1

        devices = await api.get_static_data()
        if not devices:
            print("No thermostats found on this account.", file=sys.stderr)
            return 1

        device_id = args.device_id or next(iter(devices))
        info = devices[device_id]
        print(f"Device {device_id}: {info.get('group_name') or info.get('name')}")
        if len(devices) > 1:
            print(f"({len(devices)} thermostats on this account)")

        # Scoring saved samples needs no probe and no sampling -- just the cloud.
        if args.score_from:
            hourly = load_samples(args.score_from)
            print(f"Scoring {len(hourly)} saved hour(s) from {args.score_from}")
            await score(api, device_id, hourly)
            return 0

        await probe(api, device_id)
        await compare_devices(api, devices)
        if args.probe:
            return 0

        hourly = await sample(api, device_id, args.interval, args.hours)
        # Save before scoring: the cloud lags, and an hour of sampling must not be
        # lost just because its bucket has not been published yet.
        save_samples(hourly, args.samples)
        await score(api, device_id, hourly)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
