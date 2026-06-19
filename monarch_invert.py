#!/usr/bin/env python3
"""
monarch_invert.py — Fix inverted transactions in Monarch Money.

This script helps you review inverted transactions and correct them as needed.
Inverted transactions occur when the sign of the transaction (or transaction
type -- debit or credit) in Monarch doesn't match that of the actual
transaction in your account.

Usage:
    python monarch_invert.py [options]

"""

import asyncio
import argparse
import os
import sys
from datetime import date, timedelta
from getpass import getpass

from monarchmoney import MonarchMoney
from monarchmoney.monarchmoney import CaptchaRequiredException, RequireMFAException


DEFAULT_LOOKBACK_DAYS = 90
COOKIE_FILE = "cookies.txt"


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
            print(f"  ✓ Fixed: {t['date']}  {merchant}  ({format_amount(t['amount'])} -> {format_amount(new_amount)})")
            ok += 1
        except Exception as e:
            print(f"  ✗ Failed: {t['date']}  {merchant}  — {e}")

    print(f"\nDone. {ok}/{len(selected)} transaction(s) updated.")


if __name__ == "__main__":
    asyncio.run(main())
