"""Render filing drafts from a CaseFile.

Every renderer is a pure function of the CaseFile: deterministic string
templating only. No model touches these documents. Facts (hashes, amounts,
dates, addresses) are copied from the CaseFile verbatim-or-normalized; unknown
fields render as [NOT PROVIDED] and are never guessed.

All outputs are DRAFTS for the victim to review before filing. Nothing here
is legal advice.
"""

from __future__ import annotations

from typing import Any

from .models import NOT_PROVIDED, CaseFile, Transaction

DRAFT_BANNER = (
    "> **DRAFT — for the victim's review before filing. Not legal advice.**\n"
    "> Every fact below was extracted mechanically from the story you provided.\n"
    "> Fields marked [NOT PROVIDED] must be filled in by you — they were never guessed.\n"
)

IC3_MAX_TRANSACTIONS = 10
IC3_DESCRIPTION_CHAR_LIMIT = 3500

# Official reporting channels (verified URLs).
IC3_URL = "https://www.ic3.gov"
IC3_FORM_URL = "https://complaint.ic3.gov"
FTC_URL = "https://reportfraud.ftc.gov"
SEC_URL = "https://www.sec.gov/tcr"
CFTC_URL = "https://www.cftc.gov/complaint"
FBI_RECOVERY_FRAUD_URL = "https://forms.fbi.gov/victims/cryptorecoveryfraudvictims"
ELDER_FRAUD_HOTLINE = "1-833-372-8311"
ELDER_FRAUD_URL = "https://www.justice.gov/stopelderfraud"
CHAINABUSE_URL = "https://www.chainabuse.com"


def _np(value: Any) -> str:
    return str(value) if value not in (None, "") else NOT_PROVIDED


def _usd_total(case: CaseFile):
    """Sum of USD-denominated transaction amounts, plus the non-USD leftovers.

    Never adds BTC to dollars: crypto legs without a stated USD value are
    returned separately so the victim can supply the conversion themselves.
    """
    usd = sum(
        (t.amount for t in case.transactions
         if t.amount is not None and t.currency == "USD"),
        start=0,
    )
    non_usd = [
        f"{t.amount} {t.currency or '?'}"
        for t in case.transactions
        if t.amount is not None and t.currency != "USD"
    ]
    return usd, non_usd


def _ic3_amount(t: Transaction) -> str:
    """IC3 wants amounts with no dollar signs and no commas."""
    if t.amount is None:
        return NOT_PROVIDED
    return str(t.amount)


def _asset_label(t: Transaction) -> str:
    """What actually moved, and what the figure beside it is denominated in.

    '$12,500 worth of ETH' renders as 'ETH (value stated in USD)' — the leg was
    crypto, and the dollar figure is the victim's own stated value at the time.

    The word "amount" is deliberately avoided here: ``audit._LABELED_AMOUNT_RE``
    scans for '...amount...: <number>' anywhere in a rendered document, so an
    "amount" inside this parenthetical would let the regex run past it to the
    next label's colon and read that field's number as a fabricated amount.
    """
    if t.asset and t.currency and t.asset != t.currency:
        return f"{t.asset} (value stated in {t.currency})"
    return _np(t.asset or t.currency)


def _txn_lines(t: Transaction, idx: int) -> list[str]:
    lines = [f"#### Transaction {idx}"]
    lines.append(f"- Transaction Amount (no $ or commas): {_ic3_amount(t)}")
    lines.append(f"- Currency / Asset: {_asset_label(t)}")
    lines.append(f"- Transaction Date: {_np(t.date)}")
    lines.append("- Was the money sent?: " + (NOT_PROVIDED + " (confirm Yes/No)"))
    lines.append(
        "- Transaction Type: "
        + (_np(t.method) if t.method else NOT_PROVIDED + " (e.g. Wire Transfer, Cryptocurrency)")
    )
    lines.append(f"- Transaction Hash / ID: {_np(t.tx_hash_verbatim or t.tx_hash)}")
    lines.append(
        f"- Recipient / Destination Wallet Address: "
        f"{_np(t.destination_address_verbatim or t.destination_address)}"
    )
    lines.append(f"- Originating bank/account details: {NOT_PROVIDED}")
    lines.append(f"- Recipient bank/account details: {NOT_PROVIDED}")
    return lines


def render_ic3_draft(case: CaseFile) -> str:
    """IC3 complaint draft, structured to mirror the real form's 7 steps."""
    v = case.victim
    usd_total, non_usd = _usd_total(case)
    has_amounts = any(t.amount is not None for t in case.transactions)

    lines: list[str] = []
    lines.append("# IC3 Complaint — DRAFT")
    lines.append("")
    lines.append(DRAFT_BANNER)
    lines.append(f"Case file: `{case.case_id}`")
    lines.append("")
    lines.append(f"File at: {IC3_FORM_URL} (desktop/laptop; the form cannot be saved "
                 "mid-way, and the confirmation page with your submission ID is your "
                 "only chance to save a copy — save it as a PDF).")
    lines.append("")
    lines.append("Note: the IC3 form accepts no file uploads. Screenshots and documents "
                 "must be summarized as text in Steps 5-6; keep the originals for "
                 "investigators.")
    lines.append("")

    lines.append("## Step 1 — Complaint Type")
    lines.append("- Were you the one affected by this incident?: "
                 + NOT_PROVIDED + " (Yes/No)")
    lines.append("")

    lines.append("## Step 2 — Information About You (the victim)")
    lines.append(f"- Name: {_np(v.name)}")
    lines.append(f"- Age range: {_np(v.age_range)}")
    lines.append(f"- Address: {_np(v.address)}")
    lines.append(f"- City: {_np(v.city)}")
    lines.append(f"- Country: {_np(v.country)}")
    lines.append(f"- Zip code: {_np(v.zip_code)}")
    lines.append(f"- Phone (digits only, no dashes): {_np(v.phone)}")
    lines.append(f"- Email: {_np(v.email)}")
    lines.append("")

    lines.append("## Step 3 — Financial Transaction(s)")
    lines.append("- Did you send or lose money in the incident?: "
                 + ("Yes (amounts listed below)" if has_amounts else NOT_PROVIDED))
    has_usd = any(
        t.amount is not None and t.currency == "USD" for t in case.transactions
    )
    if has_usd:
        lines.append(f"- Total loss amount (no $ or commas): {usd_total}"
                     + (" (USD-denominated amounts only)" if non_usd else ""))
    else:
        lines.append(f"- Total loss amount (no $ or commas): {NOT_PROVIDED}")
    if non_usd:
        lines.append(
            "- ALSO LOST (add the USD value yourself before filing — "
            "Recourse never estimates conversions): " + "; ".join(non_usd)
        )
    lines.append("")
    txns = case.transactions[:IC3_MAX_TRANSACTIONS]
    overflow = case.transactions[IC3_MAX_TRANSACTIONS:]
    if txns:
        for i, t in enumerate(txns, 1):
            lines.extend(_txn_lines(t, i))
            lines.append("")
    else:
        lines.append(f"- No transactions could be reconstructed: {NOT_PROVIDED}")
        lines.append("")
    if overflow:
        lines.append(
            f"NOTE: the IC3 form accepts at most {IC3_MAX_TRANSACTIONS} transactions. "
            f"The remaining {len(overflow)} transaction(s) below must go into the "
            "Step 5 'Description of Incident' box or a second complaint:"
        )
        for i, t in enumerate(overflow, IC3_MAX_TRANSACTIONS + 1):
            lines.extend(_txn_lines(t, i))
            lines.append("")

    lines.append("## Step 4 — Information About the Subject(s)")
    lines.append("All fields optional on the form; provide what you have.")
    subject_emails = case.emails
    subject_urls = case.urls
    lines.append(f"- Name: {NOT_PROVIDED}")
    if case.businesses:
        lines.append(
            "- Business Name: "
            + "; ".join(case.businesses)
            + "  <- CANDIDATE(S) taken from your story. Confirm each is the "
            "SUBJECT's business and not your own bank, exchange, or employer "
            "before filing; delete any that are not."
        )
    else:
        lines.append(f"- Business Name: {NOT_PROVIDED}")
    lines.append(f"- Address: {NOT_PROVIDED}")
    lines.append(f"- Phone (digits only, no dashes): {NOT_PROVIDED}")
    lines.append(
        "- Email(s) associated with the scam: "
        + (", ".join(subject_emails) if subject_emails else NOT_PROVIDED)
    )
    lines.append(
        "- Website(s) / app(s) the scammer used: "
        + (", ".join(subject_urls) if subject_urls else NOT_PROVIDED)
    )
    lines.append(f"- IP address: {NOT_PROVIDED}")
    lines.append("")

    lines.append("## Step 5 — Description of Incident")
    lines.append(
        f"('Describe what happened in your own words' — {IC3_DESCRIPTION_CHAR_LIMIT} "
        "character limit. Transaction and scammer-contact details matter more than "
        "narrative length.)"
    )
    lines.append("")
    lines.append("Draft description (review and edit before pasting):")
    lines.append("")
    description = case.narrative.strip()
    if len(description) > IC3_DESCRIPTION_CHAR_LIMIT:
        lines.append(
            f"NOTE: your story is {len(description)} characters — over the "
            f"{IC3_DESCRIPTION_CHAR_LIMIT}-character limit. Trim before submitting."
        )
        lines.append("")
    lines.append("```")
    lines.append(description)
    lines.append("```")
    lines.append("")

    lines.append("## Step 6 — Other Information")
    lines.append("Paste technical details as text (email headers, crypto transaction "
                 "metadata); list reports filed with other agencies.")
    if case.exchanges:
        lines.append("- Exchanges/platforms involved: " + ", ".join(case.exchanges))
    else:
        lines.append(f"- Exchanges/platforms involved: {NOT_PROVIDED}")
    lines.append(f"- Reports filed with other agencies (FTC/police/exchange ticket "
                 f"numbers): {NOT_PROVIDED}")
    lines.append("- Is this an update to a previously filed complaint?: "
                 + NOT_PROVIDED + " (Yes/No)")
    lines.append("")

    lines.append("## Step 7 — Privacy & Signature")
    lines.append("- Read the Privacy Act Statement, type your name as the digital "
                 "signature, complete the CAPTCHA, and submit.")
    lines.append("- Save the confirmation page (submission ID) immediately — it is "
                 "not shown again.")
    lines.append("")
    lines.append("---")
    lines.append("*This is a DRAFT prepared for your review. It is not legal advice "
                 "and has not been filed anywhere.*")
    return "\n".join(lines)


def render_ic3_json(case: CaseFile) -> dict[str, Any]:
    """The same IC3 mapping as structured data."""
    v = case.victim
    has_amounts = any(t.amount is not None for t in case.transactions)
    usd_total, non_usd = _usd_total(case)
    return {
        "draft": True,
        "disclaimer": "DRAFT for victim review; not legal advice; nothing filed.",
        "case_id": case.case_id,
        "step1_complaint_type": {"victim_is_filer": None},
        "step2_victim": {
            "name": v.name,
            "age_range": v.age_range,
            "address": v.address,
            "city": v.city,
            "country": v.country,
            "zip_code": v.zip_code,
            "phone": v.phone,
            "email": v.email,
        },
        "step3_financial_transactions": {
            "money_sent_or_lost": True if has_amounts else None,
            "total_loss_amount_usd": (
                str(usd_total)
                if any(
                    t.amount is not None and t.currency == "USD"
                    for t in case.transactions
                )
                else None
            ),
            "non_usd_losses_needing_conversion": non_usd,
            "transactions": [t.to_dict() for t in case.transactions[:IC3_MAX_TRANSACTIONS]],
            "overflow_transactions": [
                t.to_dict() for t in case.transactions[IC3_MAX_TRANSACTIONS:]
            ],
        },
        "step4_subjects": {
            "emails": case.emails,
            "websites": case.urls,
            # Candidates only — the victim confirms before filing.
            "business_name_candidates": case.businesses,
        },
        "step5_description": {
            "text": case.narrative.strip(),
            "char_limit": IC3_DESCRIPTION_CHAR_LIMIT,
            "over_limit": len(case.narrative.strip()) > IC3_DESCRIPTION_CHAR_LIMIT,
        },
        "step6_other_information": {
            "exchanges": case.exchanges,
            "other_agency_reports": None,
            "is_update_to_prior_complaint": None,
        },
        "step7_signature": {"note": "Typed name + CAPTCHA on the live form."},
    }


def render_freeze_letter(case: CaseFile) -> str:
    """Freeze/flag request to an exchange's fraud team.

    Conservatively worded: exchanges act per their own compliance processes or
    legal process — a freeze is requested, never promised.
    """
    exchange = case.exchanges[0] if case.exchanges else NOT_PROVIDED
    v = case.victim

    lines: list[str] = []
    lines.append("# Exchange Fraud Report & Freeze Request — DRAFT")
    lines.append("")
    lines.append(DRAFT_BANNER)
    lines.append(f"Case file: `{case.case_id}`")
    lines.append("")
    lines.append(f"To: Fraud/Security team, {exchange}")
    lines.append(f"From: {_np(v.name)} ({_np(v.email)})")
    lines.append(f"Account identifier on your platform: {NOT_PROVIDED}")
    lines.append("")
    lines.append("Subject: Fraud report — request to review and, where your policies "
                 "allow, restrict the recipient account/funds")
    lines.append("")
    lines.append("Dear Fraud/Security team,")
    lines.append("")
    lines.append(
        "I am reporting fraudulent transfers made from my account as the result of "
        "a scam. I understand that account restrictions and freezes are governed by "
        "your internal compliance processes and by legal process, and that no "
        "specific outcome is guaranteed. I am providing complete transaction "
        "details below so your team can review the recipient account and preserve "
        "records for law enforcement. Time is critical: funds move quickly once a "
        "scam is discovered."
    )
    lines.append("")
    lines.append("## Fraudulent transactions")
    if case.transactions:
        for i, t in enumerate(case.transactions, 1):
            lines.append(f"{i}. Amount: "
                         f"{_np(t.amount_verbatim or (str(t.amount) if t.amount is not None else None))}"
                         f" | Asset: {_asset_label(t)}"
                         f" | Date: {_np(t.date)}"
                         f" | Tx hash/ID: {_np(t.tx_hash_verbatim or t.tx_hash)}"
                         f" | Destination: "
                         f"{_np(t.destination_address_verbatim or t.destination_address)}")
    else:
        lines.append(f"- {NOT_PROVIDED} (no transactions could be reconstructed from "
                     "the story; add them before sending)")
    lines.append("")
    lines.append("## Destination wallet addresses observed")
    dest_addrs = sorted(
        {e.verbatim for e in case.evidence if e.kind in ("evm_address", "btc_address")}
    )
    if dest_addrs:
        for a in dest_addrs:
            lines.append(f"- {a}")
    else:
        lines.append(f"- {NOT_PROVIDED}")
    lines.append("")
    if case.businesses:
        lines.append("## Counterparty name(s) given to me")
        lines.append(
            "Named in my account of events and not independently confirmed: "
            + "; ".join(case.businesses)
            + "."
        )
        lines.append("")
    lines.append("## Timeline of the fraud")
    dates = sorted({e.value for e in case.evidence if e.kind == "date"})
    if dates:
        lines.append("Key dates extracted from my account of events: " + ", ".join(dates))
    else:
        lines.append(f"Key dates: {NOT_PROVIDED}")
    lines.append("A full written account is attached/pasted below and matches my IC3 "
                 "complaint draft.")
    lines.append("")
    lines.append("## Reference numbers")
    lines.append(f"- IC3 submission ID: {NOT_PROVIDED} (will forward once filed)")
    lines.append(f"- Police report number: {NOT_PROVIDED}")
    lines.append(f"- FTC report number: {NOT_PROVIDED}")
    lines.append("")
    lines.append("Please confirm receipt with a ticket number, preserve all records "
                 "relating to the recipient account(s), and advise what further "
                 "identity verification you require from me.")
    lines.append("")
    lines.append("Sincerely,")
    lines.append(_np(v.name))
    lines.append("")
    lines.append("---")
    lines.append("*DRAFT — review every field before sending. Not legal advice.*")
    return "\n".join(lines)


def render_action_plan(case: CaseFile) -> str:
    """Ordered next-step plan with urgency rationale and official links."""
    lines: list[str] = []
    lines.append("# Action Plan — DRAFT")
    lines.append("")
    lines.append(DRAFT_BANNER)
    lines.append(f"Case file: `{case.case_id}`")
    lines.append("")
    lines.append("Steps are ordered by urgency. Do them top to bottom.")
    lines.append("")

    exchange_str = ", ".join(case.exchanges) if case.exchanges else "the exchange(s) you used"
    lines.append("## 1. Contact the exchange fraud team(s) — NOW")
    lines.append(f"- Who: {exchange_str}.")
    lines.append(
        "- Why first: stolen crypto moves through exchanges fast; the earlier the "
        "receiving platform is alerted, the better the odds records are preserved "
        "and the recipient account is reviewed while funds are still there. "
        "Rapid, complete transaction reporting is also what IC3's recovery "
        "processes depend on."
    )
    lines.append("- Use the freeze-request draft in this case folder "
                 "(`freeze_letter.md`). A freeze is not guaranteed — exchanges act "
                 "per their own compliance processes — but a report creates the "
                 "paper trail.")
    lines.append("- Keep every ticket number and email.")
    lines.append("")

    lines.append("## 2. File the FBI IC3 complaint — same day")
    lines.append(f"- Where: {IC3_URL} (form: {IC3_FORM_URL}).")
    lines.append("- Why: IC3 is the primary federal intake for internet crime; it is "
                 "how the FBI's rapid-response processes get engaged, and other "
                 "agencies and exchanges will ask for your IC3 submission ID.")
    lines.append("- Use the draft in this case folder (`ic3_draft.md`). File even if "
                 "some fields are [NOT PROVIDED] — IC3 says to file with whatever "
                 "you have.")
    lines.append("- Desktop/laptop only; the form cannot be saved mid-way; save the "
                 "confirmation page with your submission ID.")
    lines.append("")

    lines.append("## 3. File an FTC report")
    lines.append(f"- Where: {FTC_URL}.")
    lines.append("- Why: creates a federal consumer-fraud record, feeds pattern "
                 "enforcement, and gives you another reference number institutions "
                 "recognize.")
    lines.append("")

    lines.append("## 4. File a local police report (non-emergency line)")
    lines.append("- Why: many exchanges and banks require a police report number to "
                 "escalate a fraud claim; it also anchors your paper trail locally.")
    lines.append("- Bring your case folder printouts.")
    lines.append("")

    lines.append("## Situational extras")
    lines.append(f"- Investment/securities angle: SEC tip line — {SEC_URL}.")
    lines.append(f"- Commodity/trading angle: CFTC — {CFTC_URL} or 866-366-2282.")
    lines.append("- Fiat legs (bank wire, card, Zelle): call your bank/card issuer's "
                 "fraud line and ask about a recall/dispute.")
    lines.append(f"- Aged 60+: National Elder Fraud Hotline {ELDER_FRAUD_HOTLINE} "
                 f"({ELDER_FRAUD_URL}).")
    lines.append(f"- Report the scam addresses publicly (optional): {CHAINABUSE_URL}.")
    lines.append(f"- WARNING — recovery scams: anyone who contacts you promising to "
                 f"recover your funds for a fee is almost certainly a second scam. "
                 f"The FBI has a dedicated form: {FBI_RECOVERY_FRAUD_URL}.")
    lines.append("")
    lines.append("---")
    lines.append("*DRAFT — not legal advice. Consider consulting a licensed attorney, "
                 "especially for large losses.*")
    return "\n".join(lines)


def render_unverified_report(case: CaseFile) -> str:
    """Explicit list of what could NOT be verified or was not provided."""
    lines: list[str] = []
    lines.append("# What We Could NOT Verify — DRAFT")
    lines.append("")
    lines.append(DRAFT_BANNER)
    lines.append(f"Case file: `{case.case_id}`")
    lines.append("")
    lines.append("Recourse extracts facts mechanically from your story. It does not "
                 "look anything up on-chain, contact any institution, or confirm any "
                 "identity. Everything below is either missing or unverified:")
    lines.append("")
    for note in case.unverified_notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("Before filing, fill in every [NOT PROVIDED] field you can, and "
                 "double-check each hash, address, amount, and date against your own "
                 "records (exchange history, bank statements, wallet apps).")
    return "\n".join(lines)


def render_all(case: CaseFile) -> dict[str, str]:
    """All four markdown artifacts, keyed by output filename."""
    return {
        "ic3_draft.md": render_ic3_draft(case),
        "freeze_letter.md": render_freeze_letter(case),
        "action_plan.md": render_action_plan(case),
        "unverified.md": render_unverified_report(case),
    }
