import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StrictBool, field_validator, model_validator

from blitzave import jobs
from blitzave.api.money import format_cents, parse_amount
from blitzave.refine.budgets import monthly_budget
from blitzave.refine.categorize import is_match, match_key
from blitzave.store import (
    accounts,
    categories,
    db,
    refunds,
    tags,
    transactions,
    transfers,
    users,
)

router = APIRouter(prefix="/api")


# --- request models ---------------------------------------------------------


class ConnectRequest(BaseModel):
    setup_token: str


class UpdateCheckSetting(BaseModel):
    enabled: StrictBool  # true or false only; "yes" or 1 is a 422, not a guess


class SyncMonthRequest(BaseModel):
    month: str  # "YYYY-MM"

    @field_validator("month")
    @classmethod
    def _past_or_current_month(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("month must look like 2026-10")
        if date.fromisoformat(f"{value}-01") > date.today().replace(day=1):
            raise ValueError("month must not be in the future")
        return value


class AccountTypeCorrection(BaseModel):
    account_type: Literal["asset", "liability"]
    asset_type: Literal["checking", "savings"] | None = None
    liability_type: Literal["credit_card", "loan"] | None = None

    @model_validator(mode="after")
    def _subtype_matches_account_type(self) -> "AccountTypeCorrection":
        if self.account_type == "asset" and self.asset_type is None:
            raise ValueError("asset_type is required when account_type is 'asset'")
        if self.account_type == "liability" and self.liability_type is None:
            raise ValueError(
                "liability_type is required when account_type is 'liability'"
            )
        return self


def _validate_budget(value: str | None) -> str | None:
    # parse_amount is the one money parser: it rejects exponents, NaN,
    # Infinity and fractions of a cent, and accepts a display string like
    # "$1,200.00" so the budget editor's pre-filled value round-trips.
    if value is not None:
        try:
            cents = parse_amount(value)
        except ValueError as e:
            raise ValueError("budget must be a decimal amount in whole cents") from e
        if not 0 < cents <= 2**63 - 1:
            raise ValueError("budget must be positive and fit in integer cents")
    return value


class CategoryCreate(BaseModel):
    name: str
    kind: Literal["expense", "income", "savings"]
    budget_amount: str | None = None

    _budget = field_validator("budget_amount")(_validate_budget)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value


class CategoryUpdate(BaseModel):
    name: str | None = None
    kind: Literal["expense", "income", "savings"] | None = None
    budget_amount: str | None = None

    _budget = field_validator("budget_amount")(_validate_budget)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value

    @model_validator(mode="after")
    def _something_to_change(self) -> "CategoryUpdate":
        if (
            self.name is None
            and self.kind is None
            and "budget_amount" not in self.model_fields_set
        ):
            raise ValueError("provide name, kind, and/or budget_amount")
        return self


class TagCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value


class TransferCreate(BaseModel):
    transaction_id: str  # SimpleFIN ids; either leg may come first
    counterpart_id: str


class TransactionMetadataPatch(BaseModel):
    """Only metadata is editable. Amount, date, description, account and the
    rest of the bank's record are deliberately absent from this model, so a
    request touching them is rejected at the wall."""

    categories: dict[int, float] | None = None  # {category_id: split_fraction}
    tags: list[int] | None = None
    # True = "all transactions from this merchant (see refine/categorize.py
    # is_match), now and in future syncs". Only for a single category; a split
    # is too specific to generalize.
    apply_to_all: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "TransactionMetadataPatch":
        if self.categories is None and self.tags is None:
            raise ValueError("provide categories and/or tags")
        if self.apply_to_all and (self.categories is None or len(self.categories) != 1):
            raise ValueError("apply_to_all needs exactly one category")
        if self.categories:
            for category_id, fraction in self.categories.items():
                if not 0 < fraction <= 1:
                    raise ValueError(
                        f"split fraction for category {category_id} must be in (0, 1]"
                    )
            if abs(sum(self.categories.values()) - 1.0) > 1e-6:
                raise ValueError("split fractions must sum to exactly 1.0")
        return self


# --- health / connect / sync ------------------------------------------------


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/connect")
def connect_status() -> dict[str, bool]:
    return {"connected": jobs.simplefin_connected()}


@router.post("/connect")
def connect(request: ConnectRequest) -> dict[str, bool]:
    try:
        jobs.connect_simplefin(request.setup_token)
    except jobs.ConnectError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"connected": True}


@router.post("/sync")
def sync() -> dict:
    try:
        return jobs.sync()
    except jobs.SyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sync/month")
def sync_month(body: SyncMonthRequest) -> dict:
    year, month = (int(part) for part in body.month.split("-"))
    try:
        return jobs.sync_month(year, month)
    except jobs.SyncError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# --- updates -----------------------------------------------------------------


@router.get("/updates")
def update_status() -> dict:
    # Reads what the last startup check stored; never calls the network.
    return jobs.update_status()


@router.put("/updates")
def set_update_check(body: UpdateCheckSetting) -> dict:
    jobs.set_update_check(body.enabled)
    return jobs.update_status()


# --- accounts ----------------------------------------------------------------


@router.get("/accounts")
def list_accounts() -> list[dict]:
    conn = db.connect()
    try:
        rows = accounts.list_accounts(conn)
    finally:
        conn.close()
    for row in rows:
        row["balance_display"] = (
            format_cents(row["balance_cents"])
            if row["balance_cents"] is not None
            else None
        )
    return rows


@router.patch("/accounts/{account_id}")
def correct_account_type(account_id: str, correction: AccountTypeCorrection) -> dict:
    conn = db.connect()
    try:
        try:
            accounts.update_account_type(
                conn,
                account_id,
                correction.account_type,
                correction.asset_type,
                correction.liability_type,
            )
        except accounts.AccountNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except accounts.AccountConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        # Return the full updated row, same shape as GET /api/accounts,
        # so the UI can refresh the card from the response alone.
        row = next(r for r in accounts.list_accounts(conn) if r["id"] == account_id)
    finally:
        conn.close()
    row["balance_display"] = (
        format_cents(row["balance_cents"]) if row["balance_cents"] is not None else None
    )
    return row


# --- categories ---------------------------------------------------------------


def _category_with_display(category: dict) -> dict:
    budget = category["budget_cents"]
    category["budget_display"] = None if budget is None else format_cents(budget)
    return category


@router.get("/categories")
def list_categories() -> list[dict]:
    conn = db.connect()
    try:
        return [_category_with_display(row) for row in categories.list_categories(conn)]
    finally:
        conn.close()


@router.post("/categories", status_code=201)
def create_category(request: CategoryCreate) -> dict:
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        try:
            return _category_with_display(
                categories.create_category(
                    conn,
                    user_id,
                    request.name,
                    request.kind,
                    None
                    if request.budget_amount is None
                    else parse_amount(request.budget_amount),
                )
            )
        except categories.DuplicateCategoryError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        conn.close()


@router.get("/categories/summary")
def category_summary(start: date | None = None, end: date | None = None) -> dict:
    today = date.today()
    start = start or today.replace(day=1)
    end = end or today
    if end < start:
        raise HTTPException(status_code=422, detail="end must be on or after start")
    conn = db.connect()
    try:
        category_totals, uncategorized_cents = categories.summary(
            conn, start.isoformat(), end.isoformat()
        )
    finally:
        conn.close()
    for row in category_totals:
        _category_with_display(row)
        row["total_display"] = format_cents(row["total_cents"])
        if row["kind"] == "expense":
            row.update(
                monthly_budget(
                    row["total_cents"], row["budget_cents"], start, end, today
                )
            )
            row["spent_display"] = format_cents(row["spent_cents"])
            remaining = row["remaining_cents"]
            row["remaining_display"] = (
                None if remaining is None else format_cents(remaining)
            )
            row["remaining_magnitude_display"] = (
                None if remaining is None else format_cents(abs(remaining))
            )
            projected = row["projected_cents"]
            row["projected_display"] = (
                None if projected is None else format_cents(projected)
            )
    return {
        "start": start,
        "end": end,
        "categories": category_totals,
        "uncategorized_cents": uncategorized_cents,
        "uncategorized_display": format_cents(uncategorized_cents),
    }


@router.patch("/categories/{category_id}")
def update_category(category_id: int, request: CategoryUpdate) -> dict:
    conn = db.connect()
    try:
        try:
            return _category_with_display(
                categories.update_category(
                    conn,
                    category_id,
                    request.name,
                    request.kind,
                    budget_cents=None
                    if request.budget_amount is None
                    else parse_amount(request.budget_amount),
                    budget_provided="budget_amount" in request.model_fields_set,
                )
            )
        except categories.CategoryNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except categories.DuplicateCategoryError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        conn.close()


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int) -> None:
    conn = db.connect()
    try:
        try:
            categories.delete_category(conn, category_id)
        except categories.CategoryNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except categories.CategoryInUseError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        conn.close()


# --- tags ---------------------------------------------------------------------


@router.get("/tags")
def list_tags() -> list[dict]:
    conn = db.connect()
    try:
        return tags.list_tags(conn)
    finally:
        conn.close()


@router.post("/tags", status_code=201)
def create_tag(request: TagCreate) -> dict:
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        try:
            return tags.create_tag(conn, user_id, request.name)
        except tags.DuplicateTagError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        conn.close()


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: int) -> None:
    conn = db.connect()
    try:
        try:
            tags.delete_tag(conn, tag_id)
        except tags.TagNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    finally:
        conn.close()


# --- transactions -------------------------------------------------------------


@router.get("/transactions")
def list_transactions(
    account_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    q: str | None = None,
    category_id: int | None = None,
    tag_id: int | None = None,
    sort: Literal["date", "amount", "description"] = "date",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    group_refunds: bool = False,
) -> dict:
    conn = db.connect()
    try:
        total, rows = transactions.list_transactions(
            conn,
            account_external_id=account_id,
            start=start,
            end=end,
            q=q,
            category_id=category_id,
            tag_id=tag_id,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
            group_refunds=group_refunds,
        )
    finally:
        conn.close()
    for row in rows:
        row["amount_display"] = format_cents(row["amount_cents"])
        for leg in row.get("legs", []):
            leg["amount_display"] = format_cents(leg["amount_cents"])
    return {"total": total, "limit": limit, "offset": offset, "transactions": rows}


@router.patch("/transactions/{transaction_id}")
def update_transaction_metadata(
    transaction_id: str, patch: TransactionMetadataPatch
) -> dict:
    conn = db.connect()
    try:
        try:
            current = transactions.get_transaction(conn, transaction_id)
            # Transfer legs never get a category (AGENTS.md) — they aren't
            # spending or income. Tags are still fine.
            if patch.categories is not None and current["transfer_id"] is not None:
                raise HTTPException(
                    status_code=409,
                    detail="this is part of a transfer, so it doesn't take a category",
                )
            rule_key = None
            matching_ids = None
            if patch.apply_to_all:
                rule_key = match_key(current["description"])
                if not rule_key:
                    raise HTTPException(
                        status_code=422,
                        detail="this description has no words to match on",
                    )
                matching_ids = [
                    r["id"]
                    for r in transactions.activity_descriptions(conn)
                    if is_match(match_key(r["description"]), rule_key)
                ]
            applied_to_count = transactions.update_metadata(
                conn,
                transaction_id,
                categories=patch.categories,
                tags=patch.tags,
                rule_key=rule_key,
                matching_ids=matching_ids,
            )
            row = transactions.get_transaction(conn, transaction_id)
        except transactions.TransactionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except transactions.AmbiguousTransactionError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except (transactions.UnknownCategoryError, transactions.UnknownTagError) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        conn.close()
    row["amount_display"] = format_cents(row["amount_cents"])
    if patch.apply_to_all:
        row["applied_to_count"] = applied_to_count
    return row


# --- transfers ----------------------------------------------------------------


def _transfer_with_display(transfer: dict) -> dict:
    transfer["amount_display"] = format_cents(transfer["amount_cents"])
    for leg in (transfer["debit"], transfer["credit"]):
        leg["amount_display"] = format_cents(leg["amount_cents"])
    return transfer


@router.get("/transactions/{transaction_id}/transfer-candidates")
def list_transfer_candidates(transaction_id: str) -> list[dict]:
    conn = db.connect()
    try:
        try:
            rows = transfers.list_candidates(conn, transaction_id)
        except transactions.TransactionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (
            transactions.AmbiguousTransactionError,
            transfers.AlreadyTransferLegError,
            refunds.AlreadyAdjustmentLinkedError,
        ) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        conn.close()
    for row in rows:
        row["amount_display"] = format_cents(row["amount_cents"])
    return rows


@router.get("/transfers")
def list_transfers() -> list[dict]:
    conn = db.connect()
    try:
        rows = transfers.list_transfers(conn)
    finally:
        conn.close()
    return [_transfer_with_display(row) for row in rows]


@router.post("/transfers", status_code=201)
def create_transfer(request: TransferCreate) -> dict:
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        try:
            transfer = transfers.create_transfer(
                conn, user_id, request.transaction_id, request.counterpart_id
            )
        except transactions.TransactionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (
            transactions.AmbiguousTransactionError,
            transfers.AlreadyTransferLegError,
            refunds.AlreadyAdjustmentLinkedError,
        ) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except transfers.InvalidTransferPairError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        conn.close()
    return _transfer_with_display(transfer)


@router.delete("/transfers/{transfer_id}", status_code=204)
def delete_transfer(transfer_id: int) -> None:
    conn = db.connect()
    try:
        try:
            transfers.delete_transfer(conn, transfer_id)
        except transfers.TransferNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    finally:
        conn.close()


# --- manual refunds / credits -------------------------------------------------


@router.get("/transactions/{transaction_id}/refund-candidates")
def list_refund_candidates(transaction_id: str) -> list[dict]:
    conn = db.connect()
    try:
        try:
            rows = refunds.list_candidates(conn, transaction_id)
        except transactions.TransactionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (
            transactions.AmbiguousTransactionError,
            refunds.AlreadyAdjustmentLinkedError,
        ) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        conn.close()
    for row in rows:
        row["amount_display"] = format_cents(row["amount_cents"])
    return rows


@router.get("/refunds")
def list_refunds() -> list[dict]:
    conn = db.connect()
    try:
        rows = refunds.list_refunds(conn)
    finally:
        conn.close()
    return [_transfer_with_display(row) for row in rows]


@router.post("/refunds", status_code=201)
def create_refund(request: TransferCreate) -> dict:
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        try:
            refund = refunds.create_refund(
                conn,
                user_id,
                request.transaction_id,
                request.counterpart_id,
            )
        except transactions.TransactionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (
            transactions.AmbiguousTransactionError,
            refunds.AlreadyAdjustmentLinkedError,
        ) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except refunds.InvalidRefundPairError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        conn.close()
    return _transfer_with_display(refund)


@router.delete("/refunds/{refund_id}", status_code=204)
def delete_refund(refund_id: int) -> None:
    conn = db.connect()
    try:
        try:
            refunds.delete_refund(conn, refund_id)
        except refunds.RefundNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    finally:
        conn.close()
