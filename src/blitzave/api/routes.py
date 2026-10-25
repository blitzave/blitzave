from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator, model_validator

from blitzave import jobs
from blitzave.api.money import format_cents
from blitzave.store import accounts, categories, db, tags, transactions, users

router = APIRouter(prefix="/api")


# --- request models ---------------------------------------------------------


class ConnectRequest(BaseModel):
    setup_token: str


class AccountTypeCorrection(BaseModel):
    account_type: Literal["asset", "liability"]
    asset_type: Literal["checking", "savings"] | None = None
    liability_type: Literal["credit_card", "loan"] | None = None

    @model_validator(mode="after")
    def _subtype_matches_account_type(self) -> "AccountTypeCorrection":
        if self.account_type == "asset" and self.asset_type is None:
            raise ValueError("asset_type is required when account_type is 'asset'")
        if self.account_type == "liability" and self.liability_type is None:
            raise ValueError("liability_type is required when account_type is 'liability'")
        return self


class CategoryCreate(BaseModel):
    name: str
    kind: Literal["expense", "income", "savings"]

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
        if self.name is None and self.kind is None:
            raise ValueError("provide name and/or kind")
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


class TransactionMetadataPatch(BaseModel):
    """Only metadata is editable. Amount, date, description, account and the
    rest of the bank's record are deliberately absent from this model, so a
    request touching them is rejected at the wall."""

    categories: dict[int, float] | None = None  # {category_id: split_fraction}
    tags: list[int] | None = None

    @model_validator(mode="after")
    def _validate(self) -> "TransactionMetadataPatch":
        if self.categories is None and self.tags is None:
            raise ValueError("provide categories and/or tags")
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
            format_cents(row["balance_cents"]) if row["balance_cents"] is not None else None
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


@router.get("/categories")
def list_categories() -> list[dict]:
    conn = db.connect()
    try:
        return categories.list_categories(conn)
    finally:
        conn.close()


@router.post("/categories", status_code=201)
def create_category(request: CategoryCreate) -> dict:
    conn = db.connect()
    try:
        user_id = users.get_or_create_user(conn)
        try:
            return categories.create_category(conn, user_id, request.name, request.kind)
        except categories.DuplicateCategoryError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        conn.close()


@router.get("/categories/summary")
def category_summary(start: str | None = None, end: str | None = None) -> dict:
    today = date.today()
    start = start or today.replace(day=1).isoformat()
    end = end or today.isoformat()
    conn = db.connect()
    try:
        category_totals, uncategorized_cents = categories.summary(conn, start, end)
    finally:
        conn.close()
    for row in category_totals:
        row["total_display"] = format_cents(row["total_cents"])
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
            return categories.update_category(conn, category_id, request.name, request.kind)
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
        )
    finally:
        conn.close()
    for row in rows:
        row["amount_display"] = format_cents(row["amount_cents"])
    return {"total": total, "limit": limit, "offset": offset, "transactions": rows}


@router.patch("/transactions/{transaction_id}")
def update_transaction_metadata(
    transaction_id: str, patch: TransactionMetadataPatch
) -> dict:
    conn = db.connect()
    try:
        try:
            transactions.update_metadata(
                conn, transaction_id, categories=patch.categories, tags=patch.tags
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
    return row
