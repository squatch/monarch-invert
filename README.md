# monarch_invert

Fix inverted transactions in [Monarch Money](https://www.monarchmoney.com).

Some financial institutions sync transactions with the wrong sign — debits appear as credits (positive amounts) and vice versa. Monarch doesn't let you change the sign in the UI without deleting and recreating each transaction by hand (7–8 steps each). This script lets you review the affected transactions and flip them in one shot.

## How it works

This tool uses the [monarchmoneycommunity](https://github.com/bradleyseanf/monarchmoneycommunity) Python library to access Monarch's API and correct inverted transaction data in your account.

## Requirements

- Python 3.11+
- A Monarch Money account

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install monarchmoneycommunity
```

## Usage

```bash
source .venv/bin/activate
python monarch_invert.py [options]
```

Run without arguments for more details.

### Interactive flow

1. The script lists transactions for your review
2. Enter the numbers corresponding to transactions you want to invert: `0 2 5`, `0-5`, or `all`
3. A confirmation prompt is shown to confirm the transactions to be modified
4. Upon confirmation, transactions are inverted.

