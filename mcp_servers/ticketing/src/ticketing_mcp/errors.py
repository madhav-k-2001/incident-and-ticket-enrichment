"""Domain errors for the Ticketing API.

Messages are written for the caller (an LLM or operator), so they state what went
wrong without leaking internals such as URLs or tokens.
"""


class TicketingApiError(Exception):
    """Base class for all Ticketing API failures."""


class NotFoundError(TicketingApiError):
    pass


class InvalidRequestError(TicketingApiError):
    pass


class AuthenticationError(TicketingApiError):
    pass


class UnavailableError(TicketingApiError):
    pass


class UnexpectedResponseError(TicketingApiError):
    pass


def error_for_status(status_code: int, detail: str) -> TicketingApiError:
    """Map an HTTP error response to a domain error."""
    if status_code == 404:
        return NotFoundError(detail or "Ticket not found.")
    if status_code in (401, 403):
        return AuthenticationError("Ticketing API rejected the configured credentials.")
    if status_code < 500:
        return InvalidRequestError(f"Ticketing API rejected the request: {detail}")
    return UnavailableError(f"Ticketing API failed with HTTP {status_code}.")
