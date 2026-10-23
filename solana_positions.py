import os
from decimal import Decimal

import requests


TOKEN_PROGRAMS = {
    "SPL Token": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "Token-2022": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}


def solana_rpc(method, params):
    url = os.getenv(
        "SOLANA_RPC_URL",
        "https://api.mainnet.solana.com",
    )

    response = requests.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        },
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()

    if result.get("error"):
        raise ValueError(
            f"Ошибка Solana RPC: {result['error'].get('code')}"
        )

    return result["result"]


def get_sol_balance(address):
    result = solana_rpc(
        "getBalance",
        [address, {"commitment": "confirmed"}],
    )

    return Decimal(result["value"]) / Decimal("1000000000")


def get_token_balances(address):
    balances = {}
    warnings = []

    for name, program_id in TOKEN_PROGRAMS.items():
        try:
            result = solana_rpc(
                "getTokenAccountsByOwner",
                [
                    address,
                    {"programId": program_id},
                    {
                        "encoding": "jsonParsed",
                        "commitment": "confirmed",
                    },
                ],
            )

            program_balances = {}

            for item in result["value"]:
                info = item["account"]["data"]["parsed"]["info"]
                token = info["tokenAmount"]
                ui_amount = token.get("uiAmountString")

                if ui_amount is None:
                    amount = Decimal(token["amount"]) / (
                        Decimal(10) ** token["decimals"]
                    )
                else:
                    amount = Decimal(ui_amount)

                if amount > 0:
                    mint = info["mint"]
                    program_balances[mint] = (
                        program_balances.get(mint, Decimal("0"))
                        + amount
                    )

            for mint, amount in program_balances.items():
                balances[mint] = (
                    balances.get(mint, Decimal("0")) + amount
                )

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError,
            ArithmeticError,
        ):
            warnings.append(
                f"Не удалось прочитать {name}: список неполный."
            )

    return balances, warnings

def get_token_symbols(mints):
    symbols = {}

    for start in range(0, len(mints), 30):
        batch = mints[start:start + 30]

        try:
            response = requests.get(
                "https://api.dexscreener.com/tokens/v1/solana/"
                + ",".join(batch),
                timeout=15,
            )
            response.raise_for_status()
            pairs = response.json()

            if not isinstance(pairs, list):
                continue

            for pair in pairs:
                if (
                    not isinstance(pair, dict)
                    or pair.get("chainId") != "solana"
                ):
                    continue

                for field in ("baseToken", "quoteToken"):
                    token = pair.get(field) or {}
                    mint = token.get("address")
                    symbol = token.get("symbol")

                    if (
                        mint in batch
                        and isinstance(symbol, str)
                        and symbol
                    ):
                        symbols.setdefault(
                            mint,
                            " ".join(symbol.split())[:40],
                        )

        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ):
            continue

    return symbols


def build_solana_summary(wallet):
    address = wallet["address"]

    sol = get_sol_balance(address)
    balances, warnings = get_token_balances(address)
    symbols = get_token_symbols(list(balances))

    lines = [
        f"Solana — {wallet['label']}",
        address,
        f"SOL: {sol:.9f}",
        "Токены: количество, без оценки в USD",
    ]

    if not balances and not warnings:
        lines.append("Ненулевых токен-балансов нет.")

    for mint, amount in balances.items():
        symbol = symbols.get(mint, "Токен без названия")

        lines.extend([
            f"{symbol}: {amount:f}",
            f"Mint: {mint}",
        ])

    lines.extend(warnings)

    return "\n".join(lines), not warnings
