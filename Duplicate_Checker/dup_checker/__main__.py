"""Check duplicate groups from the Comptroller duplicate spreadsheet.

Usage:
    python -m dup_checker --comptroller-data ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx
                          --duplicates Comptroller_Updates_Syeda_7.6.26_10-08-2026.xlsx
    add --group-ids 81,9 to check only some groups, --apply to update REDCap
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .checker import check_group, prescreen_group, records_needing_websites
from .data import build_records, load_groups, load_source
from .llm import OllamaClient
from .redcap import (RedcapClient, RedcapError, apply_updates, check_against_redcap,
                     plan_updates)
from .report import write_redcap_log, write_report
from .tracking import build_rows as build_tracking_rows
from .tracking import write_tracking_tab
from .website import BrowserRenderer, PageFetcher, WebsiteCheck, check_website, playwright_available


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="dup_checker", description="Check whether flagged duplicate records are true duplicates.")
    ap.add_argument("--comptroller-data", "--data", dest="comptroller_data", type=Path, required=True,
                    help="Comptroller REDCap export with every record's details "
                         "(e.g. ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx)")
    ap.add_argument("--duplicates", "--groups", dest="duplicates", type=Path, required=True,
                    help="duplicates workbook: the groups are read from its 'Assigned Duplicate Groups' sheet and "
                         "its 'Tracking Duplicates' tab is filled in (e.g. Comptroller_Updates_Syeda_7.6.26_10-08-2026.xlsx)")
    ap.add_argument("--sheet", help="sheet with the groups (default: 'Assigned Duplicate Groups')")
    ap.add_argument("--tracking-out", type=Path,
                    help="where to save the duplicates workbook with the filled-in Tracking Duplicates tab "
                         "(default: a copy named <duplicates file>_app_review_<date>_<time>.xlsx; give the duplicates file itself "
                         "to update it in place - a backup is made first)")
    ap.add_argument("--no-tracking", action="store_true", help="don't write the Tracking Duplicates tab")
    ap.add_argument("--debug", action="store_true",
                    help="also save the detailed results workbook (every record, every pair compared, every "
                         "website check) as Duplicate_Check_Results_<timestamp>.xlsx")
    ap.add_argument("--out", type=Path, help="with --debug: name of the results workbook")
    ap.add_argument("--group-ids", help="only check these group IDs, comma separated (e.g. 81,9)")
    ap.add_argument("--no-web", action="store_true", help="skip the website checks")
    ap.add_argument("--verify-all-websites", action="store_true",
                    help="check every record's website for address changes, not only the records the rules "
                         "could not settle")
    ap.add_argument("--with-ai", action="store_true",
                    help="use the local AI model (Ollama) to judge the pairs the rules can't settle. Without it, "
                         "those records are marked 'Manual review required'")
    ap.add_argument("--no-ai", action="store_true", help=argparse.SUPPRESS)   # old option; AI is now off by default
    ap.add_argument("--model", default="qwen2.5:7b", help="Ollama model (default: qwen2.5:7b)")
    ap.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama server URL")
    ap.add_argument("--workers", type=int, default=8, help="websites fetched in parallel (default: 8)")
    ap.add_argument("--timeout", type=int, default=20, help="seconds to wait for each website (default: 20)")
    ap.add_argument("--no-browser", action="store_true",
                    help="don't use a headless browser (Playwright) for websites that block plain downloads or "
                         "show no address until their scripts run")
    ap.add_argument("--apply", action="store_true",
                    help="record the decisions in REDCap (duplicate Yes/No, validation, a note in Additional "
                         "Comments). Without this flag REDCap is not contacted at all.")
    ap.add_argument("--yes", action="store_true", help="with --apply: don't ask the y/n question at the start")
    ap.add_argument("--redcap-config", type=Path, default=Path("redcap_config.py"),
                    help="file with api_url/api_token (default: redcap_config.py; or set REDCAP_API_URL "
                         "and REDCAP_API_TOKEN)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    groups_path, data_path = args.duplicates, args.comptroller_data
    for label, path in (("Duplicates workbook", groups_path), ("Comptroller data file", data_path)):
        if not path.exists():
            print(f"{label} not found: {path}", file=sys.stderr)
            return 1
    tracking_out = args.tracking_out or groups_path.with_name(
        f"{groups_path.stem}_app_review_{datetime.now():%Y-%m-%d_%H%M}{groups_path.suffix}")

    # REDCap is only contacted with --apply; connect up front so a bad token fails before the long run
    redcap, redcap_title = None, ""
    if args.apply:
        try:
            redcap = RedcapClient.from_config(args.redcap_config)
            redcap_title = redcap.project_title()
            redcap.check_fields()
        except RedcapError as exc:
            _redcap_failed(exc, args.redcap_config, during="connect")
            return 2
        print(f"REDCap          : {redcap_title}")
        if not args.yes:
            # ask now, before the long run, so the update at the end doesn't wait for anyone
            if not sys.stdin.isatty():
                print("--apply needs a confirmation, but there is no terminal to answer in. Re-run with "
                      "--apply --yes, or without --apply.", file=sys.stderr)
                return 1
            try:
                answer = input(f'This run will update REDCap project "{redcap_title}" with the duplicates it '
                               "finds.\nContinue? (y/n): ")
            except (EOFError, KeyboardInterrupt):
                answer = ""                       # no answer (input closed, or Ctrl+C) counts as "n"
                print()
            if answer.strip().lower() not in ("y", "yes"):
                print("Stopped - nothing was checked and REDCap was not changed. "
                      "Run without --apply to only fill in the Tracking Duplicates tab.")
                return 0

    print(f"Duplicates      : {groups_path.name}")
    print(f"Comptroller Data: {data_path.name}")
    groups = load_groups(groups_path, args.sheet)
    source = load_source(data_path)
    by_group = build_records(groups, source)
    if args.group_ids:
        wanted = {g.strip() for g in args.group_ids.split(",") if g.strip()}
        by_group = {g: recs for g, recs in by_group.items() if g in wanted}
        missing = wanted - set(by_group)
        if missing:
            print(f"Group IDs not in the file: {', '.join(sorted(missing))}")
    by_group = {g: recs for g, recs in by_group.items() if len(recs) > 1}
    records = [r for recs in by_group.values() for r in recs]
    print(f"Checking {len(by_group)} groups / {len(records)} records")
    if not by_group:
        return 1

    llm = None
    if args.with_ai and not args.no_ai:
        llm = OllamaClient(args.ollama_url, args.model)
        problem = llm.check()
        if problem:
            print(f"AI requested (--with-ai) but not available: {problem}.\n"
                  f"Start Ollama (and run 'ollama pull {args.model}' if the model is missing), or run without "
                  "--with-ai.", file=sys.stderr)
            return 1
        print(f"AI model        : {args.model} at {args.ollama_url}")
    else:
        print("AI              : off (add --with-ai to use the local AI model).\n"
              "                  Without the AI, the app can't judge the pairs its rules can't settle - e.g. the same\n"
              "                  address under different names, or the same name at different addresses - so those\n"
              "                  records are marked 'Manual review required'. It also can't pick an address out of\n"
              "                  website text that doesn't follow the usual address pattern.")

    # stage 1: rules on name/address/phone - no network, no AI
    prescreened = {gid: prescreen_group(gid, recs) for gid, recs in by_group.items()}
    all_prescreened = [p for pairs in prescreened.values() for p in pairs]
    settled = sum(p.settled for p in all_prescreened)
    print(f"Pre-screen: {settled} of {len(all_prescreened)} record pairs settled by rules")

    web = {}
    if not args.no_web:
        if args.verify_all_websites:
            to_check = records
        else:
            needed = set().union(*(records_needing_websites(p) for p in prescreened.values()))
            to_check = [r for r in records if r.record_id in needed]
            for r in records:
                if r.record_id not in needed and r.website:
                    web[r.record_id] = WebsiteCheck(r.record_id, r.website, "NOT_CHECKED",
                                                    "skipped - rules settled this record without the website "
                                                    "(use --verify-all-websites to check it)")
        browser = None
        if not args.no_browser:
            if playwright_available():
                browser = BrowserRenderer(timeout=args.timeout + 5)
            else:
                print("Browser         : Playwright not installed - sites that block plain downloads will be skipped "
                      "(pip install playwright && playwright install chromium)")
        fetcher = PageFetcher(timeout=args.timeout, browser=browser)
        started = time.time()
        print(f"Checking websites for {sum(1 for r in to_check if r.website)} records "
              f"({sum(1 for r in records if r.website)} have a URL)...")
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for check in pool.map(lambda r: check_website(r, fetcher, llm), to_check):
                    web[check.record_id] = check
        finally:
            if browser:
                browser.close()
        counts: dict[str, int] = {}
        for c in web.values():
            counts[c.status] = counts.get(c.status, 0) + 1
        by_browser = sum(1 for c in web.values() if c.via == "browser")
        print(f"  done in {time.time() - started:.0f}s: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
              + (f" ({by_browser} read with a browser)" if by_browser else ""))
        if browser and browser.error:
            print(f"  Browser could not start ({browser.error.__class__.__name__}) - run: playwright install chromium")

    all_pairs, all_results = [], []
    started = time.time()
    for n, (gid, recs) in enumerate(by_group.items(), 1):
        pairs, results = check_group(gid, recs, prescreened[gid], web, llm)
        all_pairs += pairs
        all_results += results
        summary = ", ".join(f"{r.record.record_id}: {r.recommendation}" for r in results)
        print(f"[{n}/{len(by_group)}] group {gid}: {summary}")
    print(f"Comparisons done in {time.time() - started:.0f}s")

    out = args.out or Path(f"Duplicate_Check_Results_{datetime.now():%Y-%m-%d_%H%M}.xlsx")
    settings = {
        "Duplicates workbook": groups_path.name,
        "Comptroller data file": data_path.name,
        "Tracking Duplicates tab written to": "off" if args.no_tracking else tracking_out.name,
        "AI model": args.model if llm else "off",
        "Website checks": "off" if args.no_web else ("all records" if args.verify_all_websites
                                                       else "only records the rules could not settle"),
        "Browser for blocked/script-built sites": "off" if args.no_browser or args.no_web else "Playwright (when installed)",
        "Group filter": args.group_ids or "all",
    }
    # the Tracking Duplicates tab first - it doesn't depend on REDCap, so a REDCap problem can't lose it
    if not args.no_tracking:
        rows = build_tracking_rows(all_results)
        try:
            write_tracking_tab(groups_path, tracking_out, rows)
            print(f"Tracking Duplicates tab ({len(rows)} rows) saved in: {tracking_out.resolve()}")
        except PermissionError:
            print(f"Could not save {tracking_out.name} - close it in Excel and run again.", file=sys.stderr)
            return 1

    plans = plan_updates(all_results)
    applied, failure = False, None
    log = Path(f"REDCap_Update_Log_{datetime.now():%Y-%m-%d_%H%M}.xlsx")
    if redcap is not None:
        try:
            applied = _apply_to_redcap(plans, redcap, redcap_title, log)
        except RedcapError as exc:
            failure = exc
        settings["REDCap"] = f"{redcap_title} - " + (
            "FAILED: " + str(failure) if failure else
            f"{sum(p.status == 'updated' for p in plans)} updated, {sum(p.status == 'skipped' for p in plans)} "
            f"skipped" if applied else "not updated")
    else:
        settings["REDCap"] = "not contacted (run with --apply to record the decisions)"

    # now that REDCap has been updated (or not), record what happened in the Tracking Duplicates tab
    if redcap is not None and not args.no_tracking and any(p.status != "planned" for p in plans):
        rows = build_tracking_rows(all_results, redcap_outcome=plans)
        try:
            write_tracking_tab(groups_path, tracking_out, rows)
            print(f"Tracking Duplicates tab updated with the REDCap results: {tracking_out.resolve()}")
        except PermissionError:
            print(f"Could not add the REDCap results to {tracking_out.name} (it is open in Excel). They are "
                  f"in the REDCap change log instead.", file=sys.stderr)
    if args.debug:
        write_report(out, all_results, all_pairs, settings, redcap_plans=plans, redcap_applied=applied or bool(failure))
        print(f"Results (debug): {out.resolve()}")
    if failure:
        _redcap_failed(failure, args.redcap_config, during="update", log=log if log.exists() else None)
        return 2
    return 0


def _redcap_failed(exc: Exception, config_path: Path, during: str, log: Path | None = None) -> None:
    """Explain a REDCap problem and how to carry on without it."""
    lines = ["", "=" * 78]
    if during == "connect":
        lines += [f"Could not connect to REDCap: {exc}.",
                  "Nothing was checked and REDCap was not changed."]
    else:
        lines += [f"Updating REDCap failed: {exc}.",
                  "The run stopped here. The Tracking Duplicates tab was already saved."]
        if log:
            lines += [f"Records that may have been changed, with their old values (for undo), are in:",
                      f"  {log.resolve()}"]
    lines += [f"Check the REDCap address and API token in {config_path} (see Duplicate_Finder_Readme.md),",
              "and the network/VPN connection. Until this is resolved, run the app WITHOUT --apply:",
              "it still checks the duplicates and fills in the Tracking Duplicates tab.",
              "=" * 78]
    print("\n".join(lines), file=sys.stderr)


def _apply_to_redcap(plans, client: RedcapClient, title: str, log: Path) -> bool:
    """Check the planned changes against live REDCap data, then write (the user confirmed at the start).
    Returns True if it wrote. Any REDCap problem raises RedcapError so the run stops."""
    if not plans:
        print("REDCap: no duplicates found, so nothing to record.")
        return False
    check_against_redcap(plans, client)
    todo = [p for p in plans if p.action == "UPDATE"]
    print(f"\nREDCap project: {title}")
    for p in plans:
        role = "kept     " if p.role == "kept" else f"dup of {p.primary_id:<3}"
        what = "update" if p.action == "UPDATE" else f"skip - {p.note}"
        print(f"  record {p.record_id:>6} [{role}] ({p.organization_name[:35]}): {what}")
    if not todo:
        print("Nothing to update - REDCap already matches.")
        apply_updates(plans, client)          # marks the skips
        return True
    # save the old values before sending anything, so even an interrupted update can be undone
    for p in todo:
        p.status = "sending"
    write_redcap_log(log, plans)
    try:
        apply_updates(plans, client)
    finally:
        write_redcap_log(log, plans)          # final status of every record
    print(f"REDCap: {sum(p.status == 'updated' for p in plans)} updated, "
          f"{sum(p.status == 'skipped' for p in plans)} skipped")
    print(f"REDCap change log (old values, for undo): {log.resolve()}")
    return True


if __name__ == "__main__":
    sys.exit(main())
