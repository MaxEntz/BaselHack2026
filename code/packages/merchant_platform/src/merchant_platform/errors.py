"""The platform API's error bodies: a `ToolError` for the §8.4 codes, a `ManagementError` for
the management site's own (a form's `problem`, the owner checks of #132, the rate limits of
#232)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.errors import AgentError, FormProblem
from core.models import ErrorCode, ManagementError, ManagementErrorCode, ToolError
from merchant_platform.accounts import NeedsInvite, NotSignedIn, OtherMerchant
from merchant_platform.limits import TooManyRequests

HTTP_STATUS: dict[ErrorCode, int] = {
    "not_found": 404,
    "slot_unavailable": 409,
    "party_too_large": 422,
    "invalid_input": 422,
    "agent_unavailable": 503,
}


def tool_error(code: ErrorCode, message: str) -> JSONResponse:
    content = ToolError(error=code, message=message).model_dump(mode="json", exclude_none=True)
    return JSONResponse(content, status_code=HTTP_STATUS[code])


def management_error(status: int, code: ManagementErrorCode, message: str) -> JSONResponse:
    body = ManagementError(error=code, message=message)
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=status)


async def agent_error(_: Request, error: AgentError) -> JSONResponse:
    return tool_error(error.code, str(error))


async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    # Field paths and reasons only: never echo the submitted values back.
    problems = "; ".join(
        f"{'.'.join(str(part) for part in issue['loc'][1:])}: {issue['msg']}"
        for issue in error.errors()
    )
    return tool_error("invalid_input", problems or "Invalid request.")


async def form_problem(_: Request, error: FormProblem) -> JSONResponse:
    body = ManagementError(error=error.code, message=str(error), problem=error.problem)
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=422)


async def not_signed_in(_: Request, error: NotSignedIn) -> JSONResponse:
    return management_error(401, "not_signed_in", str(error))


async def other_merchant(_: Request, error: OtherMerchant) -> JSONResponse:
    return management_error(403, "other_merchant", str(error))


async def needs_invite(_: Request, error: NeedsInvite) -> JSONResponse:
    return management_error(403, "needs_invite", str(error))


async def too_many_requests(_: Request, error: TooManyRequests) -> JSONResponse:
    response = management_error(429, "too_many_requests", str(error))
    response.headers["Retry-After"] = str(error.retry_after)
    return response


def add_error_handlers(app: FastAPI) -> None:
    """Every error answers its body, never a stack trace (§12)."""
    app.exception_handler(AgentError)(agent_error)
    app.exception_handler(RequestValidationError)(validation_error)
    app.exception_handler(FormProblem)(form_problem)
    app.exception_handler(NotSignedIn)(not_signed_in)
    app.exception_handler(OtherMerchant)(other_merchant)
    app.exception_handler(NeedsInvite)(needs_invite)
    app.exception_handler(TooManyRequests)(too_many_requests)
