"""Filing renderers: field mapping completeness, [NOT PROVIDED] behavior,
DRAFT labeling, and IC3 form-mechanics rules."""

from recourse.casefile import build_casefile
from recourse.filings import (
    render_action_plan,
    render_all,
    render_freeze_letter,
    render_ic3_draft,
    render_ic3_json,
    render_unverified_report,
)
from recourse.models import NOT_PROVIDED

STORY = (
    "On 2026-02-14 I sent $8,000 worth of ETH from Coinbase to "
    "0xcd34cd34cd34cd34cd34cd34cd34cd34cd34cd34. The transaction hash was "
    "0xab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12."
)
BARE_STORY = "Someone tricked me online and I feel terrible about it."


def _case(story=STORY):
    return build_casefile(story)


# --- cross-document rules -------------------------------------------------

def test_every_document_is_labeled_draft():
    for name, doc in render_all(_case()).items():
        assert "DRAFT" in doc, name


def test_every_document_disclaims_legal_advice():
    for name, doc in render_all(_case()).items():
        assert "not legal advice" in doc.lower(), name


def test_render_all_produces_four_documents():
    assert set(render_all(_case())) == {
        "ic3_draft.md",
        "freeze_letter.md",
        "action_plan.md",
        "unverified.md",
    }


# --- IC3 draft ------------------------------------------------------------

def test_ic3_draft_has_all_seven_steps():
    doc = render_ic3_draft(_case())
    for step in range(1, 8):
        assert f"## Step {step}" in doc


def test_ic3_victim_fields_render_not_provided():
    doc = render_ic3_draft(_case())
    for label in ("Name:", "Age range:", "Address:", "City:", "Country:",
                  "Zip code:", "Email:"):
        assert label in doc
    assert doc.count(NOT_PROVIDED) > 5  # unknowns are marked, never guessed


def test_ic3_amount_has_no_dollar_sign_or_commas():
    story = "On 2026-03-01 I sent $12,500 worth of ETH. Tx hash " + \
        "0x" + "ab12" * 16 + "."
    doc = render_ic3_draft(build_casefile(story))
    assert "Transaction Amount (no $ or commas): 12500" in doc


def test_ic3_total_loss_is_sum_of_transactions():
    story = (
        "On 2026-03-01 I wired $1,000 to them.\n\n"
        "On 2026-03-02 I wired $2,500 more."
    )
    doc = render_ic3_draft(build_casefile(story))
    assert "Total loss amount (no $ or commas): 3500" in doc


def test_ic3_total_never_mixes_currencies():
    story = (
        "On 2026-03-01 I sent 0.5 BTC to them. The bitcoin transaction id was "
        + "e5f6" * 16 + ".\n\nOn 2026-03-02 I wired $2,500 more."
    )
    doc = render_ic3_draft(build_casefile(story))
    assert "Total loss amount (no $ or commas): 2500 (USD-denominated amounts only)" in doc
    assert "0.5 BTC" in doc  # surfaced for manual conversion, never summed
    assert "never estimates conversions" in doc


def test_ic3_overflow_beyond_ten_transactions():
    paras = [
        f"On 2026-03-{i:02d} I wired ${i},000 to the account." for i in range(1, 13)
    ]
    doc = render_ic3_draft(build_casefile("\n\n".join(paras)))
    assert "at most 10 transactions" in doc
    assert "#### Transaction 11" in doc  # rendered in the overflow section


def test_ic3_includes_narrative_and_char_limit_warning():
    long_story = "On 2026-01-01 I sent $500 to them. " + ("padding words " * 300)
    doc = render_ic3_draft(build_casefile(long_story))
    assert "3500" in doc
    assert "over the" in doc  # over-limit warning present


def test_ic3_json_mirrors_step_structure():
    data = render_ic3_json(_case())
    assert data["draft"] is True
    for key in (
        "step1_complaint_type",
        "step2_victim",
        "step3_financial_transactions",
        "step4_subjects",
        "step5_description",
        "step6_other_information",
        "step7_signature",
    ):
        assert key in data
    assert data["step3_financial_transactions"]["total_loss_amount_usd"] == "8000"


def test_ic3_json_unknowns_are_null_not_guessed():
    data = render_ic3_json(_case())
    assert data["step2_victim"]["name"] is None
    assert data["step6_other_information"]["is_update_to_prior_complaint"] is None


# --- freeze letter --------------------------------------------------------

def test_freeze_letter_lists_transactions_and_addresses():
    doc = render_freeze_letter(_case())
    assert "0xab12" in doc.lower()
    assert "0xcd34" in doc.lower()


def test_freeze_letter_never_promises_a_freeze():
    doc = render_freeze_letter(_case())
    assert "no specific outcome is guaranteed" in doc
    assert "not guaranteed" in doc or "guaranteed" in doc


def test_freeze_letter_reference_numbers_not_provided():
    doc = render_freeze_letter(_case())
    assert f"IC3 submission ID: {NOT_PROVIDED}" in doc
    assert f"Police report number: {NOT_PROVIDED}" in doc


def test_freeze_letter_handles_no_transactions():
    doc = render_freeze_letter(_case(BARE_STORY))
    assert "no transactions could be reconstructed" in doc.lower()


# --- action plan ----------------------------------------------------------

def test_action_plan_ordering():
    doc = render_action_plan(_case())
    i_exchange = doc.index("## 1. Contact the exchange")
    i_ic3 = doc.index("## 2. File the FBI IC3")
    i_ftc = doc.index("## 3. File an FTC report")
    i_police = doc.index("## 4. File a local police report")
    assert i_exchange < i_ic3 < i_ftc < i_police


def test_action_plan_official_links():
    doc = render_action_plan(_case())
    for url in (
        "https://www.ic3.gov",
        "https://complaint.ic3.gov",
        "https://reportfraud.ftc.gov",
        "https://www.sec.gov/tcr",
        "https://www.cftc.gov/complaint",
        "https://forms.fbi.gov/victims/cryptorecoveryfraudvictims",
    ):
        assert url in doc


def test_action_plan_warns_about_recovery_scams():
    assert "recovery scams" in render_action_plan(_case())


def test_action_plan_names_detected_exchanges():
    # Rendered with the company's own capitalization, not the lowercase key.
    assert "Coinbase" in render_action_plan(_case())


# --- unverified report ----------------------------------------------------

def test_unverified_report_lists_missing_facts():
    doc = render_unverified_report(_case(BARE_STORY))
    assert "No transaction hashes/IDs were found" in doc
    assert "No monetary amounts were found" in doc
    assert "No dates were found" in doc


def test_unverified_report_always_disclaims_verification():
    doc = render_unverified_report(_case())
    assert "independently verified" in doc


# --- asset vs currency labelling ------------------------------------------

ETH_STORY = (
    "On 2026-02-14 I sent $8,000 worth of ETH from Coinbase to "
    "0xcd34cd34cd34cd34cd34cd34cd34cd34cd34cd34."
)
BIZ_STORY = (
    "On 2026-03-28 I wired 5,000 dollars to a company he called "
    '"Golden Harbor Trading LLC" and never heard back.'
)


def test_freeze_letter_labels_a_dollar_denominated_eth_leg_as_eth():
    doc = render_freeze_letter(_case(ETH_STORY))
    assert "Asset: ETH (value stated in USD)" in doc
    assert "Asset: USD" not in doc  # the bug this replaced


def test_ic3_currency_field_names_the_asset_that_moved():
    doc = render_ic3_draft(_case(ETH_STORY))
    assert "Currency / Asset: ETH (value stated in USD)" in doc


def test_asset_label_falls_back_to_currency_for_plain_fiat():
    doc = render_freeze_letter(_case("On 2026-05-02 I wired $45,000 to escrow."))
    assert "Asset: USD" in doc


def test_unverified_report_flags_the_unconverted_usd_value():
    doc = render_unverified_report(_case(ETH_STORY))
    assert "the asset sent was ETH" in doc
    assert "never converts" in doc


# --- subject business name (IC3 Step 4) -----------------------------------

def test_ic3_business_name_carries_the_candidate_and_a_confirm_instruction():
    doc = render_ic3_draft(_case(BIZ_STORY))
    assert "Business Name: Golden Harbor Trading LLC" in doc
    assert "CANDIDATE(S)" in doc


def test_ic3_business_name_is_not_provided_when_none_found():
    doc = render_ic3_draft(_case(ETH_STORY))
    assert f"- Business Name: {NOT_PROVIDED}" in doc


def test_ic3_json_exposes_business_candidates_as_candidates():
    data = render_ic3_json(_case(BIZ_STORY))
    assert data["step4_subjects"]["business_name_candidates"] == [
        "Golden Harbor Trading LLC"
    ]


def test_freeze_letter_names_the_counterparty_as_unconfirmed():
    doc = render_freeze_letter(_case(BIZ_STORY))
    assert "Golden Harbor Trading LLC" in doc
    assert "not independently confirmed" in doc


def test_freeze_letter_omits_counterparty_section_when_none_found():
    assert "Counterparty name(s)" not in render_freeze_letter(_case(ETH_STORY))


# --- stated totals on the IC3 form ----------------------------------------

def test_stated_total_is_quoted_and_reconciled_when_it_matches():
    doc = render_ic3_draft(_case(
        "I lost $10,000 total: $4,000 on 2026-04-01 and $6,000 on 2026-04-02."))
    assert "Total loss amount (no $ or commas): 10000 (the total as you stated it: '$10,000')" in doc
    assert "matches the sum of the itemized USD transfers" in doc


def test_stated_total_that_disagrees_with_itemized_sum_is_flagged():
    doc = render_ic3_draft(_case(
        "The total came to $10,000 across four transfers of $2,500 each."))
    assert "Total loss amount (no $ or commas): 10000" in doc
    assert "add up to 2500, not the total you stated" in doc


# --- exchange display names and the reframed freeze letter ------------------

def test_freeze_letter_addresses_the_exchange_by_its_own_name():
    doc = render_freeze_letter(_case())
    assert "To: Fraud/Security team, Coinbase" in doc
    assert "coinbase" not in doc.split("Case file")[1].split("## Fraudulent")[0]


def test_freeze_letter_asks_for_preservation_and_flagging_not_just_a_freeze():
    doc = render_freeze_letter(_case())
    assert "Records Preservation" in doc
    assert "preserve all records" in doc
    assert "flag the destination addresses" in doc
    assert "no specific outcome is guaranteed" in doc


def test_ic3_step_6_uses_display_names():
    assert "Exchanges/platforms involved: Coinbase" in render_ic3_draft(_case())
