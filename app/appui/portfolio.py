# app/appui/portfolio.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.extensions import db
from app.models import AccessAccount, AccessBalance, AccessAsset, Curriculum, MarketOrder, MarketTrade
from app.access_ledger.service import get_an_asset, AN_SCALE


def _get_user_wallet_account_id(user_id: str) -> str:
    acct = AccessAccount.query.filter_by(owner_user_id=user_id, account_type="user_wallet").one_or_none()
    if not acct:
        raise RuntimeError(f"No user_wallet account for user_id={user_id}")
    return acct.id


def _ticks_to_an_str(ticks: int) -> str:
    # 1000 ticks = 1.000 AN
    return f"{ticks / AN_SCALE:,.3f}"


def _compute_liquidation_value_ticks(curriculum_id: str, shares: int) -> int:
    """
    Liquidation value = fill existing bid book top-down until shares exhausted.
    Only uses current open/partial bids; assumes no new liquidity appears.
    """
    if shares <= 0:
        return 0

    remaining = int(shares)
    value_ticks = 0

    bids = (
        MarketOrder.query
        .filter_by(curriculum_id=curriculum_id, side="bid")
        .filter(MarketOrder.status.in_(("open", "partial")))
        .order_by(MarketOrder.price_ticks_per_share.desc(), MarketOrder.created_at.asc())
        .all()
    )

    for bid in bids:
        if remaining <= 0:
            break
        avail = int(bid.remaining_shares or 0)
        if avail <= 0:
            continue
        fill = avail if avail < remaining else remaining
        value_ticks += fill * int(bid.price_ticks_per_share)
        remaining -= fill

    return int(value_ticks)


def _get_last_price_ticks(curriculum_id: str) -> Optional[int]:
    t = (
        MarketTrade.query
        .filter_by(curriculum_id=curriculum_id)
        .order_by(MarketTrade.created_at.desc(), MarketTrade.id.desc())
        .first()
    )
    if not t:
        return None
    return int(t.price_ticks_per_share)


def get_user_portfolio_view(user_id: str) -> Dict:
    """
    Returns a dict suitable for template rendering.
    Values are returned both as ticks and formatted AN strings.
    """
    wallet_id = _get_user_wallet_account_id(user_id)

    an_asset = get_an_asset()

    # Cash balance (AN)
    cash_ticks = (
        AccessBalance.query
        .filter_by(account_id=wallet_id, asset_id=an_asset.id)
        .one_or_none()
    )
    cash_ticks_val = int(cash_ticks.balance) if cash_ticks else 0

    # Share holdings: all balances in wallet where asset_type == curriculum_share and balance > 0
    share_assets = (
        db.session.query(AccessBalance, AccessAsset)
        .join(AccessAsset, AccessAsset.id == AccessBalance.asset_id)
        .filter(AccessBalance.account_id == wallet_id)
        .filter(AccessAsset.asset_type == "curriculum_share")
        .filter(AccessBalance.balance > 0)
        .all()
    )

    rows: List[Dict] = []
    total_market_ticks = 0
    total_liquidation_ticks = 0

    for bal, asset in share_assets:
        curriculum_id = asset.curriculum_id
        if not curriculum_id:
            # should not happen for curriculum_share assets, but don't crash portfolio page
            continue

        curr = Curriculum.query.get(curriculum_id)
        title = curr.title if curr else f"Curriculum {curriculum_id}"

        shares = int(bal.balance)

        last_price_ticks = _get_last_price_ticks(curriculum_id)
        if last_price_ticks is None:
            market_value_ticks = 0
        else:
            market_value_ticks = shares * int(last_price_ticks)

        liquidation_value_ticks = _compute_liquidation_value_ticks(curriculum_id, shares)

        total_market_ticks += int(market_value_ticks)
        total_liquidation_ticks += int(liquidation_value_ticks)

        rows.append({
            "curriculum_id": curriculum_id,
            "title": title,
            "shares": shares,
            "last_price_ticks": last_price_ticks,
            "last_price_an": (f"{last_price_ticks / AN_SCALE:,.3f}" if last_price_ticks is not None else "—"),
            "market_value_ticks": int(market_value_ticks),
            "market_value_an": _ticks_to_an_str(int(market_value_ticks)),
            "liquidation_value_ticks": int(liquidation_value_ticks),
            "liquidation_value_an": _ticks_to_an_str(int(liquidation_value_ticks)),
        })

    # Sort biggest holdings first (by market value, fallback liquidation)
    rows.sort(key=lambda r: (r["market_value_ticks"], r["liquidation_value_ticks"]), reverse=True)

    return {
        "cash_ticks": cash_ticks_val,
        "cash_an": _ticks_to_an_str(cash_ticks_val),
        "rows": rows,
        "total_market_ticks": int(total_market_ticks),
        "total_market_an": _ticks_to_an_str(int(total_market_ticks)),
        "total_liquidation_ticks": int(total_liquidation_ticks),
        "total_liquidation_an": _ticks_to_an_str(int(total_liquidation_ticks)),
        "grand_market_ticks": int(total_market_ticks + cash_ticks_val),
        "grand_market_an": _ticks_to_an_str(int(total_market_ticks + cash_ticks_val)),
        "grand_liquidation_ticks": int(total_liquidation_ticks + cash_ticks_val),
        "grand_liquidation_an": _ticks_to_an_str(int(total_liquidation_ticks + cash_ticks_val)),
    }
