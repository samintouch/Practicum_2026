"""Decide, for each duplicate group, which records are true duplicates.

Each pair of records goes through three stages, stopping as soon as one decides:
1. pre-screen rules on name, address and phone (no network) - see prescreen_group()
2. a website rule: one record's website shows the other's address instead of its own (moved facility)
3. the local AI model, for whatever is still unclear
Websites are only fetched for records that are still undecided after stage 1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations

from .address import address_on_page, compare_addresses, find_addresses_in_text
from .data import Record
from .llm import OllamaClient
from .matching import compare_names, looks_like_person, normalize_phone, phones_match
from .website import WebsiteCheck

MIN_AI_CONFIDENCE = 0.6
MAX_ADDRESSES_FOR_MOVED_RULE = 3     # more than this and the page is probably a list of branches


@dataclass
class PairResult:
    group_id: str
    a: Record
    b: Record
    name_level: str
    name_score: float
    shared_words: list[str]
    address_level: str
    address_detail: str
    phone_match: bool | None
    zip_match: bool | None
    cross_site: str = ""             # e.g. "record A's website shows record B's phone number"
    verdict: str = "UNSURE"          # DUPLICATE, NOT_DUPLICATE, UNSURE
    confidence: float = 0.0
    decided_by: str = ""             # Rule, Website rule, AI, or "-" when nothing could decide
    reason: str = ""
    moved: Record | None = None      # record whose address looks outdated (facility moved to the other's address)

    @property
    def settled(self) -> bool:
        return bool(self.decided_by)


@dataclass
class RecordResult:
    record: Record
    recommendation: str              # "Duplicate - keep (primary)", "Duplicate of X", "Not a duplicate", "Needs review"
    duplicate_with: list[str] = field(default_factory=list)
    primary_id: str | None = None
    decided_by: str = ""
    confidence: float | None = None
    reason: str = ""
    website: WebsiteCheck | None = None
    suggested_address_change: str = ""
    draft_note: str = ""


def _yes_no(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def _decide(p: PairResult, verdict: str, confidence: float, by: str, reason: str) -> None:
    p.verdict, p.confidence, p.decided_by, p.reason = verdict, confidence, by, reason


# ---------------------------------------------------------------- stage 1: pre-screen

def _prescreen_rules(p: PairResult) -> None:
    """Clear-cut cases that need neither the website nor the AI."""
    distinct_address = p.address_level in ("DIFFERENT", "SAME_STREET")
    if p.name_level == "DIFFERENT" and distinct_address and not p.phone_match:
        _decide(p, "NOT_DUPLICATE", 0.95, "Rule",
                f"Names are different and addresses are different ({p.address_detail}).")
    elif p.name_level == "DIFFERENT" and p.address_level == "SAME_BUILDING" and not p.phone_match:
        _decide(p, "NOT_DUPLICATE", 0.85, "Rule",
                f"Different organizations in the same building ({p.address_detail}); phone numbers differ.")
    elif p.name_level in ("SAME", "SIMILAR") and p.address_level == "SAME":
        names = ("Same name" if p.name_level == "SAME"
                 else f"Similar names (shared: {', '.join(p.shared_words) or 'most words'})")
        _decide(p, "DUPLICATE", 0.95 if p.name_level == "SAME" else 0.9, "Rule",
                f"{names} at the same address ({p.address_detail}).")
    elif (p.name_level == "DIFFERENT" and p.address_level == "SAME"
          and looks_like_person(p.a.name) != looks_like_person(p.b.name)):
        # past manual reviews went both ways on these - leave it to a person
        person = p.a if looks_like_person(p.a.name) else p.b
        _decide(p, "UNSURE", 0.0, "Rule",
                f"'{person.name}' looks like an individual provider at the same address as the other "
                f"organization - may be the same practice listed under a provider's name, or a separate "
                f"provider. Check manually.")


def prescreen_group(group_id: str, records: list[Record]) -> list[PairResult]:
    pairs = []
    for a, b in combinations(records, 2):
        names = compare_names(a.name, b.name)
        addr = compare_addresses(a.address, b.address)
        p = PairResult(
            group_id=group_id, a=a, b=b,
            name_level=names.level, name_score=names.score, shared_words=names.shared_words,
            address_level=addr.level, address_detail=addr.detail,
            phone_match=phones_match(a.phone, b.phone),
            zip_match=(a.zip == b.zip) if a.zip and b.zip else None,
        )
        _prescreen_rules(p)
        pairs.append(p)
    return pairs


def records_needing_websites(pairs: list[PairResult]) -> set[str]:
    """Records in pairs the rules couldn't settle, or settled as 'needs review' - a person will look at those."""
    return {r.record_id for p in pairs if not p.settled or p.verdict == "UNSURE" for r in (p.a, p.b)}


# ---------------------------------------------------------------- stage 2: website evidence

def _cross_site(a: Record, b: Record, web: dict[str, WebsiteCheck], same_address: bool) -> str:
    """Does one record's website show the other record's address or phone number?"""
    notes = []
    for x, y, label, other in ((a, b, "A", "B"), (b, a, "B", "A")):
        check = web.get(x.record_id)
        if not check or not check.page_text:
            continue
        # only informative when the two records' addresses differ (e.g. the facility moved)
        if not same_address and y.address and y.address.number:
            hit = address_on_page(y.address, check.page_text, y.city)
            if hit and hit[0].level == "SAME":
                notes.append(f"record {label}'s website also shows record {other}'s address")
        phone = normalize_phone(y.phone)
        if phone and phone != normalize_phone(x.phone) and phone in re.sub(r"\D", "", check.page_text):
            notes.append(f"record {label}'s website shows record {other}'s phone number {y.phone}")
    return "; ".join(notes)


def _moved_rule(p: PairResult, web: dict[str, WebsiteCheck]) -> None:
    """Same name, different addresses, and one record's own website has dropped its address in
    favour of the other record's address. Either the facility moved (duplicate), or the website only
    shows the organization's main location and the record is a separate branch (not a duplicate).
    Run on all 285 groups this matched mostly multi-site organizations (MHMR centers, councils), so
    it flags the pair for manual review instead of deciding."""
    if p.name_level not in ("SAME", "SIMILAR") or p.address_level not in ("DIFFERENT", "SAME_STREET"):
        return
    for x, y in ((p.a, p.b), (p.b, p.a)):
        check = web.get(x.record_id)
        if not check or not check.page_text or not x.address or not y.address:
            continue
        if address_on_page(x.address, check.page_text, x.city):
            continue
        if len(find_addresses_in_text(check.page_text, x.city)) > MAX_ADDRESSES_FOR_MOVED_RULE:
            continue
        other = address_on_page(y.address, check.page_text, y.city)
        if other and other[0].level == "SAME":
            _decide(p, "UNSURE", 0.0, "Website rule",
                    f"Record {x.record_id}'s website does not show its REDCap address ({x.street}) but shows "
                    f"record {y.record_id}'s address ({other[1]}). Either the facility moved (duplicate) or the "
                    f"website only lists the organization's main location (separate branch). Check manually.")
            return


# ---------------------------------------------------------------- stage 3: AI

def _evidence(p: PairResult, web: dict[str, WebsiteCheck]) -> str:
    def describe(label: str, r: Record) -> str:
        check = web.get(r.record_id)
        site = check.summary() if check else "not checked"
        return (f"Record {label} (REDCap ID {r.record_id}):\n"
                f"  Name: {r.name}\n  Address: {r.full_address}\n  Phone: {r.phone or 'none'}\n"
                f"  Website: {r.website or 'none'}\n  Website check: {site}")

    return "\n".join([
        describe("A", p.a), describe("B", p.b),
        "Comparison done by code:",
        f"  Name similarity: {p.name_level} (score {p.name_score}; shared distinctive words: "
        f"{', '.join(p.shared_words) or 'none'})",
        f"  Address: {p.address_level} - {p.address_detail}",
        f"  Same phone number: {_yes_no(p.phone_match)}",
        f"  Same zip code: {_yes_no(p.zip_match)}",
        f"  Website cross-check: {p.cross_site or 'neither website shows the other record address'}",
        "Are A and B the same facility (true duplicate)?",
    ])


def _finish_pair(p: PairResult, web: dict[str, WebsiteCheck], llm: OllamaClient | None) -> None:
    p.cross_site = _cross_site(p.a, p.b, web, p.address_level in ("SAME", "SAME_BUILDING"))
    if p.settled:
        return
    _moved_rule(p, web)
    if p.settled:
        return
    if llm is None:
        _decide(p, "UNSURE", 0.0, "-",
                f"The rules can't settle this pair ({p.name_level.lower()} names, address {p.address_level.lower()}"
                f"{', same phone' if p.phone_match else ''}). Review manually, or run with --with-ai for an AI "
                f"suggestion.")
        return
    answer = llm.judge_pair(_evidence(p, web))
    if answer is None:
        _decide(p, "UNSURE", 0.0, "-", "AI did not return a usable answer - review manually.")
        return
    verdict, confidence, reason = answer
    if verdict != "UNSURE" and confidence < MIN_AI_CONFIDENCE:
        reason = f"(low confidence {confidence:.2f}, AI leaned {verdict}) {reason}"
        verdict = "UNSURE"
    _decide(p, verdict, confidence, "AI", reason)


# ---------------------------------------------------------------- per-record results

def _clusters(records: list[Record], pairs: list[PairResult]) -> list[set[str]]:
    parent = {r.record_id: r.record_id for r in records}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        if p.verdict == "DUPLICATE":
            parent[find(p.a.record_id)] = find(p.b.record_id)
    groups: dict[str, set[str]] = {}
    for r in records:
        groups.setdefault(find(r.record_id), set()).add(r.record_id)
    return list(groups.values())


def _primary(members: list[Record], web: dict[str, WebsiteCheck]) -> Record:
    def rank(r: Record):
        verified = web.get(r.record_id) is not None and web[r.record_id].status == "MATCH"
        return (r.completeness_score or 0, verified, -int(r.record_id) if r.record_id.isdigit() else 0)
    return max(members, key=rank)


def _website_note(r: Record, check: WebsiteCheck | None) -> tuple[str, str]:
    """Returns (suggested address change, sentence for the notes)."""
    if check is None or check.status == "NOT_CHECKED":
        return "", ""
    if check.status in ("DIFFERENT", "SUITE_DIFFERS") and check.suggested_address:
        change = f"{r.street} (OLD) -> {check.suggested_address}"
        return change, (f"Website ({check.final_url or check.url}) lists the address as {check.suggested_address}; "
                        f"suggest updating the address from {r.street} (OLD) to {check.suggested_address}.")
    if check.status == "DIFFERENT":
        return "", f"Website lists other addresses in {r.city} that do not match {r.street} - verify the address."
    if check.status == "OTHER_LOCATION":
        return "", ("Website only shows an address in another city/zip (likely a main office or other branch) - "
                    "address not verified; check the website link points to this location.")
    if check.status == "MATCH":
        note = f"Address confirmed on website ({check.final_url or check.url})."
    elif check.status == "UNREACHABLE":
        note = f"Website {check.url} could not be opened ({check.detail}) - verify the website URL."
    elif check.status == "INVALID_URL":
        note = f"Website value '{r.website}' is not a valid URL."
    elif check.status == "NOT_FOUND":
        note = "Website does not show a street address - address not verified."
    else:
        note = ""
    if check.name_on_page is False and check.status not in ("UNREACHABLE", "INVALID_URL"):
        note += " The website does not mention the organization name - it may belong to a different organization."
    return "", note.strip()


def check_group(group_id: str, records: list[Record], pairs: list[PairResult], web: dict[str, WebsiteCheck],
                llm: OllamaClient | None) -> tuple[list[PairResult], list[RecordResult]]:
    """Finish the pre-screened pairs (website rule, AI) and turn them into one recommendation per record."""
    for p in pairs:
        _finish_pair(p, web, llm)
    by_id = {r.record_id: r for r in records}
    results: list[RecordResult] = []

    for cluster in _clusters(records, pairs):
        members = [by_id[i] for i in sorted(cluster, key=lambda x: (len(x), x))]
        if len(members) > 1:
            primary = _primary(members, web)
            for r in members:
                dup_pairs = [p for p in pairs if p.verdict == "DUPLICATE"
                             and r.record_id in (p.a.record_id, p.b.record_id)
                             and (p.a.record_id in cluster and p.b.record_id in cluster)]
                others = [m.record_id for m in members if m.record_id != r.record_id]
                results.append(RecordResult(
                    record=r, duplicate_with=others, primary_id=primary.record_id,
                    recommendation=("Duplicate - keep (primary)" if r is primary
                                    else f"Duplicate of {primary.record_id}"),
                    decided_by="/".join(sorted({p.decided_by for p in dup_pairs})),
                    confidence=min(p.confidence for p in dup_pairs) if dup_pairs else None,
                    reason=" ".join(f"[vs {p.b.record_id if p.a is r else p.a.record_id}] {p.reason}" for p in dup_pairs),
                ))
        else:
            r = members[0]
            mine = [p for p in pairs if r.record_id in (p.a.record_id, p.b.record_id)]
            unsure = [p for p in mine if p.verdict == "UNSURE"]
            target = unsure if unsure else mine
            results.append(RecordResult(
                record=r,
                recommendation="Needs review" if unsure else "Not a duplicate",
                duplicate_with=[(p.b if p.a is r else p.a).record_id for p in unsure],
                decided_by="/".join(sorted({p.decided_by for p in target})),
                confidence=min(p.confidence for p in target) if target else None,
                reason=" ".join(f"[vs {(p.b if p.a is r else p.a).record_id}] {p.reason}" for p in target),
            ))

    moved_to = {p.moved.record_id: (p.b if p.moved is p.a else p.a) for p in pairs if p.moved}
    for res in results:
        r = res.record
        res.website = web.get(r.record_id)
        res.suggested_address_change, site_note = _website_note(r, res.website)
        if r.record_id in moved_to:
            new = moved_to[r.record_id]
            res.suggested_address_change = f"{r.street} (OLD) -> {new.full_address}"
            site_note = (f"Website ({res.website.final_url or res.website.url}) shows the facility at "
                         f"{new.full_address}; suggest updating the address from {r.street} (OLD).")
        if res.recommendation.startswith("Duplicate of"):
            lead = f"Duplicate of record {res.primary_id}."
        elif res.recommendation.startswith("Duplicate - keep"):
            lead = f"Primary record; duplicate record(s): {', '.join(res.duplicate_with)}."
        elif res.recommendation == "Not a duplicate":
            lead = "Not a duplicate - different organization/location from the other records in the group."
        else:
            lead = "Needs manual review."
        res.draft_note = " ".join(x for x in [lead, site_note] if x)

    order = {r.record_id: i for i, r in enumerate(records)}
    results.sort(key=lambda x: order[x.record.record_id])
    return pairs, results
