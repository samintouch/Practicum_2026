"""Command line: python -m comptroller_automation --ids 1-50 --input EXPORT.csv [--apply]"""

import argparse
import datetime
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from .ai import DEFAULT_MODEL, LocalAI
from .county import county_for
from .fields import NEW_LOCATIONS_PER_RECORD
from .locations import DuplicateIndex
from .normalize import phone_digits
from .records import load_records
from .redcap import RedcapError, apply_changes, load_client, new_record_values
from .report import write_report
from .scraper import Browser
from .verify import Options, verify_record


def parse_ids(tokens):
    ids = []
    for token in tokens:
        if "-" in token:
            lo, hi = token.split("-", 1)
            ids.extend(str(i) for i in range(int(lo), int(hi) + 1))
        else:
            ids.append(token)
    return ids


def confirm_project(client, assume_yes):
    info = client.project_info()
    print("=" * 72)
    print("--apply is set: this run WILL write changes to REDCap.")
    print(f"  REDCap project : {info.get('project_title')} (Project ID: {info.get('project_id')})")
    print(f"  API URL        : {client.api_url}")
    print(f"  Status         : {'PRODUCTION' if str(info.get('in_production')) == '1' else 'development'}")
    print("=" * 72)
    if assume_yes:
        return True
    return input("Proceed with writing changes to this REDCap project? [y/N]: ").strip().lower() == "y"


class _StopOnCtrlC:
    """First Ctrl+C: stop after the current record and save the report.
    Second Ctrl+C: quit at once."""

    def __init__(self):
        self.requested = False
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum, frame):
        if self.requested:
            print("\nQuitting now; no report saved.", flush=True)
            os._exit(130)
        self.requested = True
        print("\nCtrl+C: stopping after the current record and saving a report "
              "(press Ctrl+C again to quit at once)...", flush=True)


def _wait(future, stop):
    """The future's result, or None if Ctrl+C is pressed first. Waits in
    short steps: on Windows a plain wait can't be interrupted by Ctrl+C."""
    while not stop.requested:
        try:
            return future.result(timeout=0.5)
        except FutureTimeout:
            continue
    return None


def fill_from_parent(o, parent, parent_id):
    """A location page often shows no phone of its own; the new record then
    gets the parent record's, for staff to correct if needed. The fax is
    never copied: it's left blank unless the location lists its own."""
    loc = o.location
    if not loc.phone and phone_digits(parent["phone"]):
        loc.phone = phone_digits(parent["phone"])
        note = f"phone not listed for this location; taken from parent record {parent_id}"
        o.note = f"{o.note}; {note}" if o.note else note


def too_many_locations(count):
    return (f"the website gives {count} new locations for this record, more than the "
            f"{NEW_LOCATIONS_PER_RECORD} that can be added automatically, so none were added or listed; "
            f"add the ones that belong in REDCap by hand from the website")


def handle_new_locations(result, client, duplicates, dry_run):
    """Decide each other location: new record, duplicate, or left for review."""
    # the parent's phone/fax fill gaps on a new record, so use the website's
    # values when this run is updating the parent too
    parent = dict(result.record)
    if not result.blocked:
        parent.update({f.key: f.new_value for f in result.changes})
    addable = []
    for o in result.other_locations:
        if o.reason:
            continue
        match = duplicates.find(o.location)
        if match and str(match[0]).startswith("("):
            o.reason = "same address as another new location found in this run"
        elif match:
            o.reason = f"an existing REDCap record is at this address: record {match[0]} ({match[1]})"
        else:
            addable.append(o)
    # More new locations than this for one record: none are added, all wait for a person
    if len(addable) > NEW_LOCATIONS_PER_RECORD:
        # too many to add: none are added or listed; the record's note says so
        result.skipped_locations = len(addable)
        result.other_locations = [o for o in result.other_locations if o not in addable]
        note = too_many_locations(len(addable))
        result.note = f"{result.note}; {note}" if result.note else note
        return
    for o in addable:
        loc = o.location
        fill_from_parent(o, parent, result.record_id)
        o.county = county_for(loc.street, loc.city, loc.state, loc.zip) or ""
        if dry_run:
            o.new_record_id = "(dry run: would be created)"
            duplicates.add(o.new_record_id, loc.name, loc.street, loc.zip)
            continue
        try:
            values = new_record_values(loc, result.record_id, parent)
            o.new_record_id = client.create_record(values)
            duplicates.add(o.new_record_id, loc.name, loc.street, loc.zip)
        except RedcapError as exc:
            o.reason = f"failed to add new record: {exc}"
            if "rejected the API token" in str(exc):
                raise


def output_path(outdir, ids):
    numeric = sorted(int(i) for i in ids if i.isdigit())
    span = (f"{numeric[0]}_to_{numeric[-1]}" if len(numeric) > 1 else f"{numeric[0]}") if numeric else "records"
    stamp = datetime.datetime.now().strftime("%Y_%m_%d_%H%M%S")
    return Path(outdir) / f"summary_{span}_redcap_id_{stamp}.xlsx"


def _duration(seconds):
    """ "1h 05m 12s", "3m 08s" or "42s" """
    h, rest = divmod(int(round(seconds)), 3600)
    m, s = divmod(rest, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s" if m else f"{s}s"


def main(argv=None):
    started = time.time()
    p = argparse.ArgumentParser(prog="comptroller_automation",
                                description="Verify REDCap facility records against their websites.")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--ids", nargs="+", metavar="ID",
                       help="record IDs and/or ranges to check, e.g. --ids 3 5 10-20")
    which.add_argument("--all", action="store_true",
                       help="check every record in the export (can take many hours; see the README)")
    p.add_argument("--input", required=True,
                   help="REDCap export: .csv or .xlsx, labels or raw (variable-name) headers")
    p.add_argument("--apply", action="store_true", help="write confirmed changes to REDCap (default: dry run)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt with --apply")
    p.add_argument("--outdir", default="output", help="where to save the report (default: output)")
    p.add_argument("--output-xlsx", help="exact report file path, instead of the automatic name")
    p.add_argument("--skip-new-locations", action="store_true", help="don't look for other locations")
    p.add_argument("--no-ai", action="store_true", help="don't use the local AI model")
    p.add_argument("--ai-model", default=DEFAULT_MODEL, help=f"Ollama model (default: {DEFAULT_MODEL})")
    p.add_argument("--ai-suggest-only", action="store_true",
                   help="never write values found by the AI; list them for review instead")
    p.add_argument("--max-pages", type=int, default=6, help="pages to read per website (default: 6)")
    p.add_argument("--workers", type=int, default=4, help="websites checked in parallel (default: 4)")
    p.add_argument("--browser", action="store_true",
                   help="also use the headless browser for pages that look built with JavaScript")
    p.add_argument("--no-browser", action="store_true",
                   help="never use the headless browser, not even for sites that block plain requests")
    p.add_argument("--no-county", action="store_true", help="skip the Census county lookup")
    p.add_argument("--suggest-websites", action="store_true",
                   help="for records with no website, search the web and suggest one (never written to REDCap)")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")


    ids = []
    if args.ids:
        try:
            ids = parse_ids(args.ids)
        except ValueError:
            p.error(f"--ids takes record IDs and ranges like 3 5 10-20, not {' '.join(args.ids)!r}")
    if not Path(args.input).is_file():
        p.error(f"--input file not found: {args.input}")

    print(f"Please wait, reading {args.input} ...")
    try:
        records = load_records(args.input)
    except ValueError as exc:
        p.error(str(exc))
    if args.all:
        ids = list(records)
        print(f"--all: checking all {len(ids)} records in the export. This can take many hours; "
              f"press Ctrl+C once to stop and keep a report of the records done so far.")

    client = None
    if args.apply:
        try:
            client = load_client()
            if not confirm_project(client, args.yes):
                print("Cancelled; nothing written.")
                return 1
        except RedcapError as exc:
            print(f"ERROR: {exc}")
            return 1
    else:
        print("DRY RUN: no changes will be written to REDCap (pass --apply to actually write them).")

    ai = None
    if not args.no_ai:
        ai = LocalAI(args.ai_model)
        ok, why = ai.available()
        if ok:
            print(f"Local AI: {args.ai_model} (used only when a website can't be read clearly)")
        else:
            print(f"Local AI not available ({why}); continuing without it.")
            ai = None

    browser = None
    if not args.no_browser:
        try:
            import playwright  # noqa: F401
            browser = Browser(render_js=args.browser)
        except ImportError:
            print("Playwright not installed; sites that block plain requests will be reported as unreachable.")

    opts = Options(max_pages=args.max_pages, browser=browser, use_county=not args.no_county, ai=ai,
                   ai_suggest_only=args.ai_suggest_only, find_locations=not args.skip_new_locations,
                   suggest_websites=args.suggest_websites)
    duplicates = DuplicateIndex(records, client)
    found = [i for i in ids if i in records]

    print_lock = threading.Lock()

    def say(line):
        with print_lock:
            print(line, flush=True)

    def check(rid):
        if stop.requested:
            return None
        say(f"Processing Record {rid:>4}: {records[rid]['name']} ...")
        start = time.time()
        return verify_record(rid, records[rid], opts), time.time() - start

    print(f"Processing records from {ids[0]} to {ids[-1]} ({len(ids)} record(s) total)...")
    print("Press Ctrl+C once to stop and save a report of the records done so far; twice to quit at once.\n")
    stop = _StopOnCtrlC()
    results, outcomes = [], {}
    pool = ThreadPoolExecutor(max_workers=max(1, args.workers))
    futures = {}
    try:
        futures = {rid: pool.submit(check, rid) for rid in found}
        for rid in ids:
            if stop.requested:
                break
            if rid not in records:
                say(f"Skipped    Record {rid:>4}: ID NOT FOUND")
                continue
            done = _wait(futures[rid], stop)
            if done is None:            # Ctrl+C while this record was still being checked
                break
            result, seconds = done
            results.append(result)
            # A Ctrl+C from here on only takes effect before the next record,
            # so a REDCap write is never cut off halfway.
            if client and result.changes and not result.blocked:
                try:
                    outcomes[rid] = apply_changes(client, result)
                except RedcapError as exc:
                    outcomes[rid] = (False, f"error: {exc}")
                    if "rejected the API token" in str(exc):
                        print(exc)
                        break
            if opts.find_locations:
                try:
                    handle_new_locations(result, client, duplicates, dry_run=client is None)
                except RedcapError as exc:
                    print(exc)
                    break
            say(f"Finished   Record {rid:>4}: {result.record['name']} ({seconds:.1f}s)")
    finally:
        # don't wait for websites or AI calls still running after a Ctrl+C
        pool.shutdown(wait=not stop.requested, cancel_futures=True)
        if browser and not stop.requested:
            browser.close()

    if stop.requested:
        # Records that finished checking out of order (a later one while an
        # earlier one waited on the AI) still go in the report. They aren't
        # written to REDCap: the run was stopped.
        reported = {r.record_id for r in results}
        late = [futures[rid].result()[0] for rid in found if rid in futures and rid not in reported
                and futures[rid].done() and not futures[rid].cancelled() and futures[rid].exception() is None
                and futures[rid].result() is not None]
        results.extend(late)
        print(f"\nStopped by Ctrl+C: {len(results)} of {len(found)} record(s) finished and are in the report"
              + (f" ({len(late)} of them checked but not written to REDCap)" if client and late else "") + ".")
    path = Path(args.output_xlsx) if args.output_xlsx else output_path(args.outdir, ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_report(path, results, outcomes, dry_run=client is None, suggest_websites=args.suggest_websites)

    updated = sum(1 for ok, _ in outcomes.values() if ok)
    review = sum(1 for r in results if r.reviews or r.status in ("no website", "fetch failed"))
    new_ids = [o.new_record_id for r in results for o in r.other_locations if o.new_record_id]
    print(f"\n{len(results)} record(s) checked: {updated} updated in REDCap, {review} need manual review, "
          f"{len(new_ids)} new location record(s) {'would be ' if client is None else ''}added.")
    print(f"Results workbook: {path}")
    print(f"Total time: {_duration(time.time() - started)}")
    if stop.requested:
        # unfinished website checks and AI calls are still running in the
        # background; end the process now instead of waiting for them
        sys.stdout.flush()
        os._exit(130)
    return 0


if __name__ == "__main__":
    sys.exit(main())
