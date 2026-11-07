import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StrictBool, field_validator, model_validator

from blitzave import jobs
from blitzave.api.money import format_cents, parse_amount
from blitzave.refine.budgets import monthly_budget
from blitzave.refine.categorize import is_match, match_key
from blitzave.refine.goals import savings_by_account
from blitzave.refine.transfers import kind_of
from blitzave.store import (
    accounts,
    categories,
    db,
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
    # targeted: a savings account that holds goals (migration 009)
    asset_type: Literal["checking", "savings", "targeted"] | None = None
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


# How a category's amount repeats (migration 008). Rounds are
# calendar-aligned, so a period has to divide the year evenly.
Schedule = Literal["once", "recurring"]
PeriodMonths = Literal[1, 2, 3, 4, 6, 12]
SCHEDULE_FIELDS = ("schedule", "period_months", "target_on", "ends_on")
GOAL_REQUEST_FIELDS = ("goal_level", "account_id", "completed_on")


def _check_plan(model: "CategoryCreate | CategoryUpdate") -> None:
    """A plan is an amount plus its schedule, checked as a whole. An amount
    with no schedule means a monthly one, which is what every budget was
    before schedules existed (and what the budget editor still sends)."""
    if model.budget_amount is None:
        if any(getattr(model, f) is not None for f in SCHEDULE_FIELDS):
            raise ValueError(
                "schedule, period_months, target_on and ends_on need a budget_amount"
            )
        return
    if (model.schedule or "recurring") == "once":
        if model.target_on is None:
            raise ValueError("a one-time amount needs a target_on date")
        if model.period_months is not None or model.ends_on is not None:
            raise ValueError("period_months and ends_on are only for recurring amounts")
    elif model.target_on is not None:
        raise ValueError("target_on is only for one-time amounts; use ends_on")


# A goal's level (migration 009). account_id is the public account id.
GoalLevel = Literal["critical", "regular"]


def _check_goal_home(model: "CategoryCreate | CategoryUpdate") -> None:
    if (model.goal_level is None) != (model.account_id is None):
        raise ValueError("a goal needs both goal_level and account_id")


def _plan(model: "CategoryCreate | CategoryUpdate") -> dict:
    """The validated request as the five columns the store saves."""
    if model.budget_amount is None:
        return dict(categories.NO_PLAN)
    schedule = model.schedule or "recurring"
    recurring = schedule == "recurring"
    return {
        "budget_cents": parse_amount(model.budget_amount),
        "schedule": schedule,
        "period_months": (model.period_months or 1) if recurring else None,
        "target_on": None if recurring else model.target_on.isoformat(),
        "ends_on": model.ends_on.isoformat() if recurring and model.ends_on else None,
    }


class CategoryCreate(BaseModel):
    name: str
    kind: Literal["expense", "income", "savings"]
    budget_amount: str | None = None
    schedule: Schedule | None = None
    period_months: PeriodMonths | None = None
    target_on: date | None = None
    ends_on: date | None = None
    goal_level: GoalLevel | None = None
    account_id: str | None = None

    _budget = field_validator("budget_amount")(_validate_budget)

    @model_validator(mode="after")
    def _plan_is_whole(self) -> "CategoryCreate":
        _check_plan(self)
        _check_goal_home(self)
        if self.goal_level is not None:
            if self.kind != "savings":
                raise ValueError("only a savings category can be a goal")
            if self.budget_amount is None:
                raise ValueError("a goal needs a budget_amount")
        return self

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
    schedule: Schedule | None = None
    period_months: PeriodMonths | None = None
    target_on: date | None = None
    ends_on: date | None = None
    goal_level: GoalLevel | None = None
    account_id: str | None = None
    completed_on: date | None = None

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
        sent = self.model_fields_set
        if (
            self.name is None
            and self.kind is None
            and not sent & {"budget_amount", *GOAL_REQUEST_FIELDS}
        ):
            raise ValueError(
                "provide name, kind, budget_amount, goal_level and account_id,"
                " or completed_on"
            )
        # Sending any schedule field means "here is the whole plan", so it is
        # checked as one. budget_amount alone keeps the current schedule.
        if sent & set(SCHEDULE_FIELDS):
            _check_plan(self)
        # A goal's home is its level and account together; both null means
        # it's no longer a goal.
        if sent & {"goal_level", "account_id"}:
            if not {"goal_level", "account_id"} <= sent:
                raise ValueError("send goal_level and account_id together")
            _check_goal_home(self)
        return self


class GoalOrder(BaseModel):
    goal_ids: list[int]  # every active goal in the account, top first

    @field_validator("goal_ids")
    @classmethod
    def _each_goal_once(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("goal_ids must list the account's goals")
        if len(set(value)) != len(value):
            raise ValueError("each goal can appear only once")
        return value


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
        except (accounts.AccountConflictError, accounts.AccountHoldsGoalsError) as e:
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
        goal = (
            None
            if request.account_id is None
            else {"goal_level": request.goal_level, "account_id": request.account_id}
        )
        try:
            return _category_with_display(
                categories.create_category(
                    conn, user_id, request.name, request.kind, _plan(request), goal
                )
            )
        except categories.UnknownAccountError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except (categories.DuplicateCategoryError, categories.GoalRuleError) as e:
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
                    row["total_cents"],
                    # Pace for longer rounds comes with the next step; until
                    # then only a monthly amount is measured against a month.
                    row["budget_cents"] if row["period_months"] == 1 else None,
                    start,
                    end,
                    today,
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


def _updated_plan(conn, category_id: int, request: CategoryUpdate) -> dict | None:
    """None leaves the amount and schedule alone. Any schedule field sends a
    whole new plan. budget_amount on its own changes only the amount (or,
    when null, removes the plan), keeping the schedule the category had."""
    sent = request.model_fields_set
    if sent & set(SCHEDULE_FIELDS):
        return _plan(request)
    if "budget_amount" not in sent:
        return None
    if request.budget_amount is None:
        return dict(categories.NO_PLAN)
    current = categories.get_category(conn, category_id)
    if current["schedule"] is None:
        return _plan(request)  # a first amount: monthly
    kept = {f: current[f] for f in categories.PLAN_FIELDS}
    return {**kept, "budget_cents": parse_amount(request.budget_amount)}


def _updated_goal(conn, category_id: int, request: CategoryUpdate) -> dict | None:
    """None leaves the goal alone. Otherwise the whole goal as it should be:
    the current one with what the request sent laid over it."""
    sent = request.model_fields_set
    if not sent & set(GOAL_REQUEST_FIELDS):
        return None
    current = categories.get_category(conn, category_id)
    goal = {f: current[f] for f in GOAL_REQUEST_FIELDS}
    if "account_id" in sent:  # goal_level always comes with it
        goal["goal_level"] = request.goal_level
        goal["account_id"] = request.account_id
        if request.account_id is None:
            goal["completed_on"] = None  # no longer a goal, so nothing to complete
    if "completed_on" in sent:
        goal["completed_on"] = (
            None if request.completed_on is None else request.completed_on.isoformat()
        )
    return goal


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
                    plan=_updated_plan(conn, category_id, request),
                    goal=_updated_goal(conn, category_id, request),
                )
            )
        except categories.CategoryNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except categories.UnknownAccountError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except (categories.DuplicateCategoryError, categories.GoalRuleError) as e:
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


# --- goals --------------------------------------------------------------------


def _savings(conn) -> dict:
    """Every targeted savings account with its goals filled in line, and the
    completed goals, with display strings added at the edge."""
    view = savings_by_account(accounts.list_accounts(conn), categories.list_categories(conn))
    view["total_display"] = format_cents(view["total_cents"])
    for home in view["accounts"]:
        balance = home["balance_cents"]
        home["balance_display"] = None if balance is None else format_cents(balance)
        home["unassigned_display"] = format_cents(home["unassigned_cents"])
        for goal in home["goals"]:
            _category_with_display(goal)
            goal["saved_display"] = format_cents(goal["saved_cents"])
            goal["to_go_display"] = format_cents(goal["to_go_cents"])
    for goal in view["completed"]:
        _category_with_display(goal)
    return view


@router.get("/goals")
def list_goals() -> dict:
    conn = db.connect()
    try:
        return _savings(conn)
    finally:
        conn.close()


@router.put("/accounts/{account_id}/goal-order")
def reorder_goals(account_id: str, request: GoalOrder) -> dict:
    """Returns the account as GET /api/goals shows it, so the page can redraw
    the line, and the money's new split, from the response alone."""
    conn = db.connect()
    try:
        try:
            categories.reorder_goals(conn, account_id, request.goal_ids)
        except categories.UnknownAccountError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except categories.GoalOrderError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return next(a for a in _savings(conn)["accounts"] if a["id"] == account_id)
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
    kind: Literal["transaction", "transfer", "refund", "credit"] | None = None,
    group_pairs: bool = False,
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
            kind=kind,
            group_pairs=group_pairs,
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
            # A paired leg (transfer, refund or credit) never gets a category
            # (AGENTS.md) — it isn't spending or income. Tags are still fine.
            if patch.categories is not None and current["transfer_id"] is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"this is part of a {current['transfer_kind']}, "
                    "so it doesn't take a category",
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


# --- transfers: every matched pair --------------------------------------------
# One table, one set of endpoints. What a pair is called (transfer, refund,
# credit) is worked out for display by kind_of, never stored.

PairKind = Literal["transfer", "refund", "credit"]


def _transfer_with_display(transfer: dict) -> dict:
    same_account = transfer.pop("same_account")
    transfer["kind"] = kind_of(
        same_account, transfer["debit"]["description"], transfer["credit"]["description"]
    )
    transfer["amount_display"] = format_cents(transfer["amount_cents"])
    for leg in (transfer["debit"], transfer["credit"]):
        leg["amount_display"] = format_cents(leg["amount_cents"])
    return transfer


@router.get("/transactions/{transaction_id}/transfer-candidates")
def list_transfer_candidates(transaction_id: str) -> list[dict]:
    """The exact opposite amount, in any account. Each candidate's `kind` is
    what linking it would make."""
    conn = db.connect()
    try:
        try:
            current = transactions.get_transaction(conn, transaction_id)
            rows = transfers.list_candidates(conn, transaction_id)
        except transactions.TransactionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (
            transactions.AmbiguousTransactionError,
            transfers.AlreadyTransferLegError,
        ) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        conn.close()
    for row in rows:
        row["kind"] = kind_of(row.pop("same_account"), current["description"], row["description"])
        row["amount_display"] = format_cents(row["amount_cents"])
    return rows


@router.get("/transfers")
def list_transfers(kind: PairKind | None = None) -> list[dict]:
    """Every pair, newest first; `kind` narrows it to one kind."""
    conn = db.connect()
    try:
        rows = transfers.list_transfers(conn)
    finally:
        conn.close()
    pairs = [_transfer_with_display(row) for row in rows]
    return [p for p in pairs if kind is None or p["kind"] == kind]


@router.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: int) -> dict:
    """One pair with both legs: what the transaction editor shows as "linked to"."""
    conn = db.connect()
    try:
        try:
            row = transfers.get_transfer(conn, transfer_id)
        except transfers.TransferNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    finally:
        conn.close()
    return _transfer_with_display(row)


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
