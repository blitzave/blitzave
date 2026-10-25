from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from blitzave import jobs
from blitzave.store import accounts, db

router = APIRouter(prefix="/api")


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


@router.get("/accounts")
def list_accounts() -> list[dict]:
    conn = db.connect()
    try:
        return accounts.list_accounts(conn)
    finally:
        conn.close()


@router.patch("/accounts/{account_id}")
def correct_account_type(account_id: int, correction: AccountTypeCorrection) -> dict:
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
    finally:
        conn.close()
    return {"id": account_id, "account_type": correction.account_type}
