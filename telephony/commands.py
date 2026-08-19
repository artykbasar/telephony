from __future__ import annotations

import json
from pathlib import Path

import click
from frappe.commands import get_site, pass_context
from frappe.utils import get_bench_path

from telephony.runtime.health import read_runtime_status
from telephony.runtime.manager import TelephonyRuntimeManager, discover_telephony_sites, site_runtime_disabled
from telephony.runtime.process import TelephonyRuntimeProcess


@click.command("telephony-runtime")
@pass_context
def telephony_runtime(context) -> None:
    site = get_site(context)
    bench_path = Path(get_bench_path()).resolve()
    if site_runtime_disabled(site=site, bench_path=bench_path):
        raise click.UsageError("Telephony runtime is disabled for this site by telephony_runtime_disabled.")
    click.echo(f"Starting Telephony native SIP runtime for site {site}.")
    try:
        TelephonyRuntimeProcess(site=site, bench_path=bench_path).run()
    except RuntimeError as exc:
        raise click.UsageError(str(exc)) from exc


@click.command("telephony-runtime-manager")
@pass_context
def telephony_runtime_manager(context) -> None:
    bench_path = Path(get_bench_path()).resolve()
    TelephonyRuntimeManager(bench_path=bench_path, site_provider=lambda: discover_telephony_sites(bench_path)).run()


@click.command("telephony-runtime-status")
@click.option("--json-output", is_flag=True, help="Print machine-readable JSON.")
@pass_context
def telephony_runtime_status(context, json_output: bool) -> None:
    bench_path = Path(get_bench_path()).resolve()
    sites = discover_telephony_sites(bench_path)
    rows = []
    healthy = True
    for site in sites:
        status = read_runtime_status(site=site, bench_path=bench_path)
        registrations = status.get("registrations") or {}
        row = {
            "site": site,
            "ready": bool(status.get("ready")),
            "state": status.get("state") or "stopped",
            "pid": int(status.get("pid") or 0),
            "voice_running": bool(status.get("voice_running")),
            "registration_count": len(registrations),
            "registered_count": sum(value == "registered" for value in registrations.values()),
        }
        healthy = healthy and row["ready"]
        rows.append(row)

    if json_output:
        click.echo(json.dumps({"healthy": healthy, "sites": rows}, separators=(",", ":")))
    elif not rows:
        click.echo("No Telephony sites with enabled native SIP agents are configured.")
    else:
        for row in rows:
            label = "READY" if row["ready"] else "NOT READY"
            click.echo(
                f"{row['site']}: {label} pid={row['pid']} "
                f"registrations={row['registered_count']}/{row['registration_count']}"
            )
    if not healthy:
        raise click.exceptions.Exit(1)


@click.command("telephony-local-https")
@pass_context
def telephony_local_https(context) -> None:
    from telephony.local_https.proxy import run_local_https_proxy

    site = get_site(context)
    bench_path = Path(get_bench_path()).resolve()
    try:
        run_local_https_proxy(site=site, bench_path=bench_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.UsageError(str(exc)) from exc



commands = [telephony_runtime, telephony_runtime_manager, telephony_runtime_status, telephony_local_https]
