"""Check duplicate groups from the Comptroller duplicate spreadsheet.

Usage:
    python -m checker --data ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx
                       --dup Comptroller_Updates_Syeda_7.6.26_10-08-2026.xlsx
    add --group-ids 81,9 to check only some groups, --apply to update REDCap
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# the rest of the app's modules (pandas, openpyxl, requests, playwright under the hood) are imported
# inside main(), after the "Please wait" message - those libraries alone take a couple of seconds to
# load, and importing them up here would delay that message by the same amount, every run.

OUTPUT_DIR = Path("output")
REDCAP_CONFIG = Path("redcap_config.py")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="checker", description="Check whether flagged duplicate records are true duplicates.")
    ap.add_argument("--data", "--comptroller-data", dest="comptroller_data", type=Path, required=True,
                    help="Comptroller REDCap export with every record's details, as .xlsx or .csv "
                         "(e.g. ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx)")
    ap.add_argument("--dup", "--groups", dest="duplicates", type=Path, required=True,
                    help="duplicates workbook: the groups are read from its 'Assigned Duplicate Groups' sheet and "
                         "its 'Tracking Duplicates' tab is filled in (e.g. Comptroller_Updates_Syeda_7.6.26_10-08-2026.xlsx)")
    ap.add_argument("--debug", action="store_true",
                    help="also save the detailed results workbook (every record, every pair compared, every "
                         "website check) as Duplicate_Check_Results_<timestamp>.xlsx")
    ap.add_argument("--out", type=Path, help="with --debug: name of the results workbook")
    ap.add_argument("--group-ids", help="only check these group IDs, comma separated (e.g. 81,9)")
    ap.add_argument("--limit", type=int,
                    help="only process the first N records across all groups - handy for a quick test run "
                         "(e.g. --limit 20). A group left with just one record after the cut is dropped, "
                         "since there's nothing left to compare it against")
    ap.add_argument("--no-web", action="store_true", help="skip the website checks")
    ap.add_argument("--verify-all-websites", action="store_true",
                    help="check every record's website for address changes, not only the records the rules "
                         "could not settle")
    ap.add_argument("--no-ai", action="store_true",
                    help="skip the local AI model (Ollama). By default the app uses it to judge the pairs the "
                         "rules can't settle; with --no-ai, those are marked 'Manual review required' instead")
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
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    groups_path, data_path = args.duplicates, args.comptroller_data
    print(f"Please wait, processing: {data_path.name}", flush=True)

    from .checker import check_group, prescreen_group, records_needing_websites
    from .data import build_records, load_groups, load_source
    from .llm import OllamaClient
    from .redcap import RedcapClient, RedcapError, plan_updates
    from .report import add_redcap_sheet, write_report
    from .tracking import build_rows as build_tracking_rows
    from .tracking import write_tracking_tab
    from .website import BrowserRenderer, PageFetcher, WebsiteCheck, check_website, normalize_url, playwright_available

    for label, path in (("Duplicates workbook", groups_path), ("Comptroller data file", data_path)):
        if not path.exists():
            print(f"{label} not found: {path}", file=sys.stderr)
            return 1
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tracking_out = OUTPUT_DIR / f"{groups_path.stem}_app_review_{datetime.now():%Y-%m-%d_%H%M}{groups_path.suffix}"

    # REDCap is only contacted with --apply; connect up front so a bad token fails before the long run
    redcap, redcap_title = None, ""
    if args.apply:
        try:
            redcap = RedcapClient.from_config(REDCAP_CONFIG)
            redcap_title = redcap.project_title()
            redcap.check_fields()
        except RedcapError as exc:
            _redcap_failed(exc, REDCAP_CONFIG, during="connect")
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
    groups = load_groups(groups_path, None)
    source = load_source(data_path)
    by_group = build_records(groups, source)
    if args.group_ids:
        wanted = {g.strip() for g in args.group_ids.split(",") if g.strip()}
        by_group = {g: recs for g, recs in by_group.items() if g in wanted}
        missing = wanted - set(by_group)
        if missing:
            print(f"Group IDs not in the file: {', '.join(sorted(missing))}")
    by_group = {g: recs for g, recs in by_group.items() if len(recs) > 1}
    if args.limit is not None:
        limited: dict[str, list] = {}
        total = 0
        for g, recs in by_group.items():
            if total >= args.limit:
                break
            take = recs[:args.limit - total]
            if len(take) > 1:              # a lone leftover record has nothing left to compare it against
                limited[g] = take
                total += len(take)
        by_group = limited
    records = [r for recs in by_group.values() for r in recs]
    print(f"Checking {len(by_group)} groups / {len(records)} records")
    if not by_group:
        return 1

    llm = None
    if args.no_ai:
        print("AI              : off (--no-ai).\n"
              "                  Without the AI, the app can't judge the pairs its rules can't settle - e.g. the same\n"
              "                  address under different names, or the same name at different addresses - so those\n"
              "                  records are marked 'Manual review required'. It also can't pick an address out of\n"
              "                  website text that doesn't follow the usual address pattern.")
    else:
        llm = OllamaClient(args.ollama_url, args.model)
        problem = llm.check()
        if problem:
            llm = None
            print(f"AI              : off - not available ({problem}).\n"
                  f"                  Start Ollama (and run 'ollama pull {args.model}' if the model is missing) to "
                  "enable it, or add\n                  --no-ai to continue quietly without it.", file=sys.stderr)
        else:
            print(f"AI model        : {args.model} at {args.ollama_url}")

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
        def _visit(r):
            print(f"  record {r.record_id}: {normalize_url(r.website) or r.website}")
            return check_website(r, fetcher, llm)

        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for check in pool.map(_visit, to_check):
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

    out = args.out or OUTPUT_DIR / f"Duplicate_Check_Results_{datetime.now():%Y-%m-%d_%H%M}.xlsx"
    settings = {
        "Duplicates workbook": groups_path.name,
        "Comptroller data file": data_path.name,
        "Tracking Duplicates tab written to": tracking_out.name,
        "AI model": args.model if llm else "off",
        "Website checks": "off" if args.no_web else ("all records" if args.verify_all_websites
                                                       else "only records the rules could not settle"),
        "Browser for blocked/script-built sites": "off" if args.no_browser or args.no_web else "Playwright (when installed)",
        "Group filter": args.group_ids or "all",
    }
    # the Tracking Duplicates tab first - it doesn't depend on REDCap, so a REDCap problem can't lose it
    rows = build_tracking_rows(all_results, pairs=all_pairs)
    try:
        write_tracking_tab(groups_path, tracking_out, rows)
        print(f"Tracking Duplicates tab ({len(rows)} rows) saved in: {tracking_out.resolve()}")
    except PermissionError:
        print(f"Could not save {tracking_out.name} - close it in Excel and run again.", file=sys.stderr)
        return 1

    plans = plan_updates(all_results)
    applied, failure = False, None
    if redcap is not None:
        try:
            applied = _apply_to_redcap(plans, redcap, redcap_title)
        except RedcapError as exc:
            failure = exc
        settings["REDCap"] = f"{redcap_title} - " + (
            "FAILED: " + str(failure) if failure else
            f"{sum(p.status == 'updated' for p in plans)} updated, {sum(p.status == 'skipped' for p in plans)} "
            f"skipped" if applied else "not updated")
    else:
        settings["REDCap"] = "not contacted (run with --apply to record the decisions)"

    # now that REDCap has been updated (or not), record what happened in the Tracking Duplicates tab
    if redcap is not None and any(p.status != "planned" for p in plans):
        rows = build_tracking_rows(all_results, redcap_outcome=plans, pairs=all_pairs)
        try:
            write_tracking_tab(groups_path, tracking_out, rows)
            print(f"Tracking Duplicates tab updated with the REDCap results: {tracking_out.resolve()}")
        except PermissionError:
            print(f"Could not add the REDCap results to {tracking_out.name} (it is open in Excel). They are "
                  f"in the REDCap Updates tab instead.", file=sys.stderr)
    if redcap is not None and plans:
        try:
            add_redcap_sheet(tracking_out, plans)
            print(f"REDCap Updates tab (old values, for undo) added to: {tracking_out.resolve()}")
        except PermissionError:
            print(f"Could not add the REDCap Updates tab to {tracking_out.name} - close it in Excel and run "
                  "again.", file=sys.stderr)
    if args.debug:
        write_report(out, all_results, all_pairs, settings, redcap_plans=plans, redcap_applied=applied or bool(failure))
        print(f"Results (debug): {out.resolve()}")
    if failure:
        _redcap_failed(failure, REDCAP_CONFIG, during="update", tracking_out=tracking_out)
        return 2
    return 0


def _redcap_failed(exc: Exception, config_path: Path, during: str, tracking_out: Path | None = None) -> None:
    """Explain a REDCap problem and how to carry on without it."""
    lines = ["", "=" * 78]
    if during == "connect":
        lines += [f"Could not connect to REDCap: {exc}.",
                  "Nothing was checked and REDCap was not changed."]
    else:
        lines += [f"Updating REDCap failed: {exc}.",
                  "The run stopped here. The Tracking Duplicates tab was already saved."]
        if tracking_out:
            lines += ["Records that may have been changed, with their old values (for undo), are in the "
                      "'REDCap Updates' tab of:",
                      f"  {tracking_out.resolve()}"]
    lines += [f"Check the REDCap address and API token in {config_path} (see Duplicate_Checker_Readme.md),",
              "and the network/VPN connection. Until this is resolved, run the app WITHOUT --apply:",
              "it still checks the duplicates and fills in the Tracking Duplicates tab.",
              "=" * 78]
    print("\n".join(lines), file=sys.stderr)


def _apply_to_redcap(plans, client: RedcapClient, title: str) -> bool:
    """Check the planned changes against live REDCap data, then write (the user confirmed at the start).
    Returns True if it wrote. Any REDCap problem raises RedcapError so the run stops."""
    from .redcap import apply_updates, check_against_redcap
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
    for p in todo:
        p.status = "sending"
    apply_updates(plans, client)
    print(f"REDCap: {sum(p.status == 'updated' for p in plans)} updated, "
          f"{sum(p.status == 'skipped' for p in plans)} skipped")
    return True


if __name__ == "__main__":
    sys.exit(main())
