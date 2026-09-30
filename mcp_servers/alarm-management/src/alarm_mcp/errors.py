"""Domain errors for the Alarm Management API.

Messages are written for the caller (an LLM or operator), so they state what went
wrong and what to do next, without leaking internals such as URLs or tokens.
"""


class AlarmApiError(Exception):
    """Base class for all Alarm Management API failures."""


class NotFoundError(AlarmApiError):
    pass


class InvalidRequestError(AlarmApiError):
    pass


class AuthenticationError(AlarmApiError):
    pass


class UnavailableError(AlarmApiError):
    pass


class UnexpectedResponseError(AlarmApiError):
    pass


def error_for_status(status_code: int, detail: str) -> AlarmApiError:
    """Map an HTTP error response to a domain error."""
    if status_code == 404:
        return NotFoundError(f"Not found: {detail}. Verify the identifier, e.g. via search_assets or list_alarms.")
    if status_code in (401, 403):
        return AuthenticationError("Alarm API rejected the credentials. Check ALARM_API_TOKEN.")
    if status_code in (400, 422):
        return InvalidRequestError(f"Alarm API rejected the request: {detail}")
    return UnavailableError(f"Alarm API failed with status {status_code}. Try again later.")
