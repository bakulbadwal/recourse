"""Extractor precision/recall tests. No model, no network — pure functions."""

from decimal import Decimal

from recourse.intake import (
    extract,
    extract_amounts,
    extract_dates,
    extract_exchanges,
    extract_hashes_and_addresses,
    extract_urls_and_emails,
)

EVM_HASH = "0x" + "ab12" * 16  # 64 hex chars
EVM_ADDR = "0x" + "cd34" * 10  # 40 hex chars
BTC_TXID = "e5f6" * 16  # bare 64 hex
BECH32 = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
BASE58 = "1Kz3QvPr8fJdWm5xTn7hYb2cRg4sLuE9aB"


# --- hashes and addresses -------------------------------------------------

def test_evm_tx_hash_extracted():
    items = extract_hashes_and_addresses(f"the hash was {EVM_HASH} yesterday")
    assert [i.value for i in items if i.kind == "evm_tx_hash"] == [EVM_HASH]


def test_evm_hash_normalized_lowercase_but_verbatim_preserved():
    mixed = "0x" + "AB12" * 16
    items = extract_hashes_and_addresses(f"hash {mixed}")
    (item,) = [i for i in items if i.kind == "evm_tx_hash"]
    assert item.value == mixed.lower()
    assert item.verbatim == mixed


def test_evm_address_extracted():
    items = extract_hashes_and_addresses(f"sent to {EVM_ADDR}.")
    assert [i.value for i in items if i.kind == "evm_address"] == [EVM_ADDR]


def test_truncated_evm_hash_not_extracted():
    truncated = EVM_HASH[:-1]  # 63 hex chars
    items = extract_hashes_and_addresses(f"partial hash {truncated} here")
    assert items == []


def test_overlong_hex_not_extracted():
    overlong = EVM_HASH + "f"  # 65 hex chars
    items = extract_hashes_and_addresses(f"weird string {overlong} here")
    assert items == []


def test_hex_with_invalid_char_not_extracted():
    bad = "0x88df016429689c079f3b2f6adg9fa052532c56795b733da78a91ebe6a71394"
    items = extract_hashes_and_addresses(f"claimed hash {bad}")
    assert items == []


def test_39_char_address_not_extracted():
    short = "0x" + "a" * 39
    assert extract_hashes_and_addresses(f"address {short}") == []


def test_bare_64_hex_requires_transaction_context():
    no_context = f"my puzzle string is {BTC_TXID} which I made up"
    assert extract_hashes_and_addresses(no_context) == []
    with_context = f"the bitcoin transaction id was {BTC_TXID}"
    items = extract_hashes_and_addresses(with_context)
    assert [i.value for i in items] == [BTC_TXID]
    assert items[0].kind == "btc_txid"


def test_bech32_extracted_without_context():
    items = extract_hashes_and_addresses(f"paid {BECH32} online")
    assert [i.value for i in items] == [BECH32]
    assert items[0].kind == "btc_address"


def test_base58_requires_address_context():
    assert extract_hashes_and_addresses(f"random token {BASE58} appeared") == []
    items = extract_hashes_and_addresses(f"his wallet address was {BASE58}")
    assert [i.value for i in items] == [BASE58]


def test_base58_case_preserved():
    items = extract_hashes_and_addresses(f"deposit address {BASE58}")
    assert items[0].value == BASE58  # never lowercased — base58 is case-sensitive


# --- amounts --------------------------------------------------------------

def test_symbol_amounts():
    items = extract_amounts("I sent $1,234.56 and later €500.")
    assert {i.value for i in items} == {"1234.56 USD", "500 EUR"}


def test_code_amounts():
    items = extract_amounts("transferred 0.75 BTC then 2,500.50 USDT")
    assert {i.value for i in items} == {"0.75 BTC", "2500.50 USDT"}


def test_word_amounts():
    items = extract_amounts("wired 5,000 dollars to them")
    assert [i.value for i in items] == ["5000 USD"]


def test_amount_verbatim_is_exact_source_substring():
    text = "I lost $12,500 that day."
    (item,) = extract_amounts(text)
    assert item.verbatim in text
    assert item.verbatim == "$12,500"


def test_bare_number_without_currency_not_an_amount():
    assert extract_amounts("my account number is 99887766") == []


# --- dates ----------------------------------------------------------------

def test_iso_date():
    (item,) = extract_dates("it happened on 2026-02-14, a Saturday")
    assert item.value == "2026-02-14"


def test_month_name_date():
    (item,) = extract_dates("On March 3, 2026 I sent it")
    assert item.value == "2026-03-03"


def test_slash_date_us_reading():
    (item,) = extract_dates("paid on 3/28/2026 by wire")
    assert item.value == "2026-03-28"


def test_slash_date_unambiguous_day_first():
    (item,) = extract_dates("second payment on 25/12/2026")
    assert item.value == "2026-12-25"


def test_invalid_calendar_date_rejected():
    assert extract_dates("code 2026-13-40 is not a date") == []


def test_ambiguous_slash_date_gets_note():
    result = extract("first payment on 03/04/2026 by wire")
    assert any("ambiguous" in n for n in result.notes)


# --- urls, emails, exchanges ---------------------------------------------

def test_url_trailing_punctuation_stripped():
    (item,) = extract_urls_and_emails("see https://scam.example.net/app.")
    assert item.value == "https://scam.example.net/app"


def test_email_extracted():
    items = extract_urls_and_emails("he wrote from bad.actor@mail.example daily")
    assert [i.value for i in items] == ["bad.actor@mail.example"]


def test_exchange_dictionary():
    items = extract_exchanges("I used Coinbase and later Kraken.")
    assert {i.value for i in items} == {"coinbase", "kraken"}


def test_binance_us_not_double_counted_as_binance():
    items = extract_exchanges("my Binance.US account")
    assert [i.value for i in items] == ["binance.us"]


# --- transactions ---------------------------------------------------------

def test_transaction_groups_cooccurring_facts():
    story = (
        f"On 2026-02-14 I sent $8,000 worth of ETH to the address\n{EVM_ADDR}. "
        f"The transaction hash was\n{EVM_HASH}."
    )
    result = extract(story)
    assert len(result.transactions) == 1
    t = result.transactions[0]
    assert t.amount == Decimal("8000")
    assert t.date == "2026-02-14"
    assert t.tx_hash == EVM_HASH
    assert t.destination_address == EVM_ADDR
    assert t.method == "Cryptocurrency"


def test_no_transaction_invented_from_loose_date():
    result = extract("We first talked on 2026-01-05 about nothing in particular.")
    assert result.transactions == []
    assert [e.value for e in result.evidence] == ["2026-01-05"]


def test_wire_method_detected():
    result = extract("On 2026-05-02 I wired $45,000 to the escrow account.")
    assert result.transactions[0].method == "Wire Transfer"
