#!/usr/bin/env python3
"""
monarch_invert.py — Fix inverted transactions in Monarch Money.

This script helps you review inverted transactions and correct them as needed.
Inverted transactions occur when the sign of the transaction (or transaction
type -- debit or credit) in Monarch doesn't match that of the actual
transaction in your account.

Usage:
    python monarch_invert.py [options]

By default the script selects transactions tagged "Is Inverted" (--use-tags
mode). Pass date-range or filter flags (--start, --end, --date, --days,
--positive, --negative, --all, or --account-name) to browse by date range
instead.

"""

import asyncio
import argparse
import json
import os
import sys
from datetime import date, timedelta
from getpass import getpass

from monarchmoney import MonarchMoney
from monarchmoney.monarchmoney import CaptchaRequiredException, RequireMFAException


DEFAULT_LOOKBACK_DAYS = 90
COOKIE_FILE = "cookies.txt"
TAG_IS_INVERTED = "Is Inverted"
TAG_WAS_INVERTED = "Was Inverted"
TAG_COLOR_DEFAULT = "#808080"
MM_DIR = ".mm"
PREFS_FILE = os.path.join(MM_DIR, "monarch_invert_prefs.json")


def valid_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{s}'. Expected format: YYYY-MM-DD")


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--start", metavar="YYYY-MM-DD", type=valid_date, help="Start date (default: 90 days ago)")
    parser.add_argument("--end", metavar="YYYY-MM-DD", type=valid_date, help="End date (default: today)")
    parser.add_argument("--date", metavar="YYYY-MM-DD", type=valid_date, help="Shorthand for --start and --end on the same day")
    parser.add_argument("--days", metavar="N", type=int, help=f"Look back N days from today (default: {DEFAULT_LOOKBACK_DAYS})")
    parser.add_argument(
        "--account-name",
        metavar="NAME",
        default="",
        help="Case-insensitive substring to filter by account name. Omit to show all accounts.",
    )
    parser.add_argument(
        "--use-tags",
        action="store_true",
        help=(
            f'Select transactions tagged "{TAG_IS_INVERTED}" instead of browsing by date range '
            "(default when no date-range or filter flags are given; overridden by "
            "--start, --end, --date, --days, --positive, --negative, --all, or --account-name). "
            "You will still be asked to confirm before any changes are made."
        ),
    )
    filter_group = parser.add_mutually_exclusive_group()
    filter_group.add_argument(
        "--positive",
        action="store_true",
        help="Show only positive (credit) transactions.",
    )
    filter_group.add_argument(
        "--negative",
        action="store_true",
        help="Show only negative (debit) transactions.",
    )
    filter_group.add_argument(
        "--all",
        action="store_true",
        help="Show all transactions regardless of sign (default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview which transactions would be flipped without making any changes.",
    )
    parser.add_argument(
        "--save-credentials",
        action="store_true",
        help="Save the session to disk after login so subsequent runs skip the login prompt. "
             "Do not use on shared computers. You must prevent others from accessing .mm/mm_session.pickle. You should remove it when it is no longer needed.",
    )
    tag_create_group = parser.add_mutually_exclusive_group()
    tag_create_group.add_argument(
        "--create-tags",
        action="store_true",
        help=(
            f'Create missing "{TAG_IS_INVERTED}" and "{TAG_WAS_INVERTED}" tags automatically '
            "without prompting."
        ),
    )
    tag_create_group.add_argument(
        "--no-create-tags",
        action="store_true",
        help="Do not prompt for or create missing tags.",
    )
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)
    return parser.parse_args()


def _secure_save_session(mm: MonarchMoney) -> None:
    mm.save_session()
    session_file = mm._session_file
    if os.path.exists(session_file):
        os.chmod(session_file, 0o600)


async def login(mm: MonarchMoney, save_credentials: bool) -> None:
    print("Monarch Money Login")
    email = input("  Email: ").strip()
    password = getpass("  Password: ")
    try:
        await mm.login(email, password, save_session=False)
        if save_credentials:
            _secure_save_session(mm)
        print("  Logged in successfully.\n")
    except RequireMFAException:
        otp = getpass("  Two-factor code: ").strip()
        try:
            await mm.multi_factor_authenticate(email, password, otp)
            if save_credentials:
                _secure_save_session(mm)
            print("  Logged in with MFA.\n")
        except CaptchaRequiredException:
            _print_captcha_instructions()
    except CaptchaRequiredException:
        _print_captcha_instructions()
    except Exception as e:
        err = str(e)
        if "429" in err or "Too Many Requests" in err:
            print(
                "\n  Monarch Money rate-limited the login attempt (HTTP 429).\n"
                "  Please wait a few minutes and try again.\n"
            )
            sys.exit(1)
        raise


def _print_captcha_instructions() -> None:
    print(
        "\n  Monarch Money is requiring CAPTCHA verification, which blocks headless login.\n"
        "  This typically happens after several failed login attempts.\n"
        "\n"
        "  To work around this, log in via browser cookies:\n"
        f"  1. Create a file called '{COOKIE_FILE}' in this directory.\n"
        "  2. Open https://app.monarch.money in your browser and log in.\n"
        "  3. Open DevTools → Network tab → reload the page.\n"
        "  4. Click any request to app.monarch.money → Request Headers → 'Cookie:'.\n"
        f"  5. Copy the full Cookie header value and paste it into {COOKIE_FILE}.\n"
        "  6. Re-run this script. The session will be saved and you can delete\n"
        f"     {COOKIE_FILE} afterward.\n"
    )
    sys.exit(1)


async def do_login(mm: MonarchMoney, save_credentials: bool) -> None:
    if save_credentials:
        try:
            mm.load_session()
            print("Reusing saved session.\n")
            return
        except Exception:
            pass

    if os.path.exists(COOKIE_FILE):
        mode = os.stat(COOKIE_FILE).st_mode & 0o777
        if mode & 0o077:
            print(
                f"\n  Error: {COOKIE_FILE} is readable by group or others (permissions: {oct(mode)}).\n"
                f"  Run: chmod 600 {COOKIE_FILE}\n"
            )
            sys.exit(1)
        with open(COOKIE_FILE) as f:
            cookie_string = f.read().strip()
        if cookie_string.lower().startswith("cookie:"):
            cookie_string = cookie_string[7:].strip()
        print(f"Found {COOKIE_FILE}, authenticating with browser cookies...")
        await mm.login_with_cookies(cookie_string, save_session=False)
        if save_credentials:
            _secure_save_session(mm)
            print(f"  Logged in. Session saved. You can delete {COOKIE_FILE} now.\n")
        else:
            print("  Logged in.\n")
        return

    await login(mm, save_credentials)


async def get_accounts(mm: MonarchMoney, name_substring: str) -> list[dict]:
    data = await mm.get_accounts()
    accounts = data.get("accounts", [])
    if name_substring:
        accounts = [a for a in accounts if name_substring.lower() in a.get("displayName", "").lower()]
    return accounts


def _load_prefs() -> dict:
    if not os.path.exists(PREFS_FILE):
        return {}
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_prefs(prefs: dict) -> None:
    os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
    with open(PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(PREFS_FILE, 0o600)


async def _refresh_tags_cache(mm: MonarchMoney, tags_cache: dict[str, dict]) -> None:
    data = await mm.get_transaction_tags()
    for t in data.get("householdTransactionTags", []):
        tags_cache[t["name"].lower()] = t


async def _get_tag(mm: MonarchMoney, name: str, tags_cache: dict[str, dict]) -> dict | None:
    """Return tag dict for *name*, or None if it doesn't exist.

    *tags_cache* is a mapping of lowercase tag name -> tag dict and is updated
    in-place so repeat calls don't hit the network.
    """
    key = name.lower()
    if key not in tags_cache:
        await _refresh_tags_cache(mm, tags_cache)
    return tags_cache.get(key)


async def _create_tag(mm: MonarchMoney, name: str, tags_cache: dict[str, dict]) -> dict | None:
    result = await mm.create_transaction_tag(name=name, color=TAG_COLOR_DEFAULT)
    new_tag = result.get("createTransactionTag", {}).get("tag", {})
    if new_tag and new_tag.get("name"):
        tags_cache[new_tag["name"].lower()] = new_tag
        return new_tag
    await _refresh_tags_cache(mm, tags_cache)
    return tags_cache.get(name.lower())


async def resolve_tag_ids(
    mm: MonarchMoney,
    tags_cache: dict[str, dict],
    create_tags: bool,
    no_create_tags: bool,
) -> tuple[str | None, str | None]:
    is_inverted_tag = await _get_tag(mm, TAG_IS_INVERTED, tags_cache)
    was_inverted_tag = await _get_tag(mm, TAG_WAS_INVERTED, tags_cache)

    missing_names = []
    if not is_inverted_tag:
        missing_names.append(TAG_IS_INVERTED)
    if not was_inverted_tag:
        missing_names.append(TAG_WAS_INVERTED)

    if not missing_names:
        return is_inverted_tag["id"], was_inverted_tag["id"]

    if no_create_tags:
        print(f'Skipping creation of missing tags: {", ".join(missing_names)}.')
        return (
            is_inverted_tag["id"] if is_inverted_tag else None,
            was_inverted_tag["id"] if was_inverted_tag else None,
        )

    should_create = create_tags
    if not should_create:
        prefs = _load_prefs()
        if prefs.get("declined_tag_creation_prompt"):
            print("Skipping tag creation prompt (previously declined).")
        else:
            print(f'Missing tag(s): {", ".join(missing_names)}.')
            answer = input("Create missing tag(s) now? [Y/n] ").strip().lower()
            if answer == "n":
                prefs["declined_tag_creation_prompt"] = True
                _save_prefs(prefs)
                print("Okay, not creating tags. Use --create-tags later to create them.")
            else:
                should_create = True

    if should_create:
        if not is_inverted_tag:
            is_inverted_tag = await _create_tag(mm, TAG_IS_INVERTED, tags_cache)
        if not was_inverted_tag:
            was_inverted_tag = await _create_tag(mm, TAG_WAS_INVERTED, tags_cache)

    return (
        is_inverted_tag["id"] if is_inverted_tag else None,
        was_inverted_tag["id"] if was_inverted_tag else None,
    )


async def update_tags_after_invert(
    mm: MonarchMoney,
    transaction: dict,
    is_inverted_tag_id: str | None,
    was_inverted_tag_id: str | None,
) -> None:
    """Remove the 'Is Inverted' tag and add the 'Was Inverted' tag."""
    if not is_inverted_tag_id or not was_inverted_tag_id:
        return
    current_tag_ids: set[str] = {tg["id"] for tg in (transaction.get("tags") or [])}

    new_tag_ids = (current_tag_ids - {is_inverted_tag_id}) | {was_inverted_tag_id}
    if new_tag_ids != current_tag_ids:
        await mm.set_transaction_tags(
            transaction_id=transaction["id"],
            tag_ids=list(new_tag_ids),
        )


def format_amount(amount: float) -> str:
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:,.2f}"


def print_transaction(i: int, t: dict) -> None:
    amount = t["amount"]
    merchant = t.get("merchant", {}) or {}
    merchant_name = merchant.get("name") or t.get("plaidName") or "(unknown)"
    category = (t.get("category") or {}).get("name", "")
    acct_name = (t.get("account") or {}).get("displayName", "")
    date_str = t.get("date", "")
    notes = t.get("notes") or ""
    note_str = f"  notes: {notes}" if notes else ""
    label = "credit" if amount > 0 else "debit"
    print(
        f"  [{i:3d}] {date_str}  {format_amount(amount):>12}  ({label})"
        f"  {merchant_name}  [{category}]  {acct_name}{note_str}"
    )


def parse_selection(selection: str, count: int) -> list[int]:
    indices: set[int] = set()
    if selection.lower() == "all":
        return list(range(count))
    for token in selection.replace(",", " ").split():
        if "-" in token:
            parts = token.split("-")
            try:
                lo, hi = int(parts[0]), int(parts[1])
                indices.update(range(lo, hi + 1))
            except ValueError:
                print(f"  Skipping unrecognized range: {token}")
        else:
            try:
                indices.add(int(token))
            except ValueError:
                print(f"  Skipping unrecognized token: {token}")
    return [i for i in sorted(indices) if 0 <= i < count]


async def main() -> None:
    args = parse_args()

    today = date.today()
    start_date = (args.start or args.date or today - timedelta(days=args.days or DEFAULT_LOOKBACK_DAYS)).isoformat()
    end_date = (args.end or args.date or today).isoformat()

    mm = MonarchMoney()
    await do_login(mm, args.save_credentials)

    tags_cache: dict[str, dict] = {}
    is_inverted_tag_id, was_inverted_tag_id = await resolve_tag_ids(
        mm,
        tags_cache,
        create_tags=args.create_tags,
        no_create_tags=args.no_create_tags,
    )

    date_range_mode = bool(
        args.start or args.end or args.date or args.days
        or args.positive or args.negative or args.all
        or args.account_name
    )
    use_tags = args.use_tags or not date_range_mode

    if use_tags:
        # Resolve the "Is Inverted" tag; it must already exist to filter by it
        print(f'Looking for transactions tagged "{TAG_IS_INVERTED}"...')
        if not is_inverted_tag_id:
            print(f'No "{TAG_IS_INVERTED}" tag found in your account. Nothing to do.')
            sys.exit(0)

        txn_data = await mm.get_transactions(
            tag_ids=[is_inverted_tag_id],
            limit=500,
        )
        candidates = txn_data.get("allTransactions", {}).get("results", [])

        if not candidates:
            print(f'No transactions tagged "{TAG_IS_INVERTED}" found.')
            sys.exit(0)

        print(f'Found {len(candidates)} transaction(s) tagged "{TAG_IS_INVERTED}":\n')
        for i, t in enumerate(candidates):
            print_transaction(i, t)

        print()
        dry_run_prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"{dry_run_prefix}About to flip {len(candidates)} transaction(s):")
        for t in candidates:
            merchant = (t.get("merchant") or {}).get("name") or t.get("plaidName") or "(unknown)"
            print(f"  {t['date']}  {format_amount(t['amount']):>12}  ->  {format_amount(-t['amount']):>12}  {merchant}")

        if args.dry_run:
            print("\nDry run — no changes made.")
            sys.exit(0)

        confirm = input("\nConfirm? [Y/n] ").strip().lower()
        if confirm == "n":
            print("Aborted.")
            sys.exit(0)

        print()
        ok = 0
        for t in candidates:
            new_amount = -t["amount"]
            merchant = (t.get("merchant") or {}).get("name") or t.get("plaidName") or "(unknown)"
            try:
                await mm.update_transaction(transaction_id=t["id"], amount=new_amount)
                await update_tags_after_invert(mm, t, is_inverted_tag_id, was_inverted_tag_id)
                print(f"  ✓ Fixed: {t['date']}  {merchant}  ({format_amount(t['amount'])} -> {format_amount(new_amount)})")
                ok += 1
            except Exception as e:
                print(f"  ✗ Failed: {t['date']}  {merchant}  — {e}")

        print(f"\nDone. {ok}/{len(candidates)} transaction(s) updated.")
        return

    # --- Standard date-range / sign-filter selection ---

    # Find accounts
    filter_desc = f" matching '{args.account_name}'" if args.account_name else ""
    print(f"Looking for accounts{filter_desc}...")
    accounts = await get_accounts(mm, args.account_name)

    if not accounts:
        print(f"No accounts found matching '{args.account_name}'.")
        print("Your accounts:")
        for a in await get_accounts(mm, ""):
            print(f"  - {a.get('displayName')}  (id: {a.get('id')})")
        sys.exit(1)

    print(f"Found {len(accounts)} account(s):")
    for a in accounts:
        print(f"  - {a.get('displayName')}  (id: {a.get('id')})")
    print()

    # Fetch transactions
    account_ids = [a["id"] for a in accounts]
    print(f"Fetching transactions from {start_date} to {end_date}...")
    data = await mm.get_transactions(
        start_date=start_date,
        end_date=end_date,
        account_ids=account_ids,
        limit=500,
    )

    all_txns = data.get("allTransactions", {}).get("results", [])
    print(f"Found {len(all_txns)} total transaction(s).\n")

    if args.positive:
        candidates = [t for t in all_txns if t["amount"] > 0]
        print(f"Found {len(candidates)} positive (credit) transaction(s):")
    elif args.negative:
        candidates = [t for t in all_txns if t["amount"] < 0]
        print(f"Found {len(candidates)} negative (debit) transaction(s):")
    else:
        candidates = all_txns
        print(f"Showing all {len(candidates)} transaction(s):")

    if not candidates:
        print("No transactions to show.")
        sys.exit(0)

    print()
    for i, t in enumerate(candidates):
        print_transaction(i, t)

    print()
    print("Enter the numbers of transactions to FLIP (negate their amount).")
    print("Examples:  '0 2 5'  or  '0-5'  or  'all'  or  press Enter to cancel.")
    selection = input("> ").strip()

    if not selection:
        print("No changes made.")
        sys.exit(0)

    indices = parse_selection(selection, len(candidates))
    selected = [candidates[i] for i in indices]

    if not selected:
        print("No valid transactions selected.")
        sys.exit(0)

    dry_run_prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{dry_run_prefix}About to flip {len(selected)} transaction(s):")
    for t in selected:
        merchant = (t.get("merchant") or {}).get("name") or t.get("plaidName") or "(unknown)"
        print(f"  {t['date']}  {format_amount(t['amount']):>12}  ->  {format_amount(-t['amount']):>12}  {merchant}")

    if args.dry_run:
        print("\nDry run — no changes made.")
        sys.exit(0)

    confirm = input("\nConfirm? [Y/n] ").strip().lower()
    if confirm == "n":
        print("Aborted.")
        sys.exit(0)

    print()
    ok = 0
    for t in selected:
        new_amount = -t["amount"]
        merchant = (t.get("merchant") or {}).get("name") or t.get("plaidName") or "(unknown)"
        try:
            await mm.update_transaction(transaction_id=t["id"], amount=new_amount)
            await update_tags_after_invert(mm, t, is_inverted_tag_id, was_inverted_tag_id)
            print(f"  ✓ Fixed: {t['date']}  {merchant}  ({format_amount(t['amount'])} -> {format_amount(new_amount)})")
            ok += 1
        except Exception as e:
            print(f"  ✗ Failed: {t['date']}  {merchant}  — {e}")

    print(f"\nDone. {ok}/{len(selected)} transaction(s) updated.")


if __name__ == "__main__":
    asyncio.run(main())
