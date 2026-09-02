"""
HTTP client for communicating with the JHipster backend.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time

from .models import (
    NextQueuedInteractionDTO,
    MatchResultSubmissionDTO,
    InteractionDTO,
)

logger = logging.getLogger(__name__)


class NashiumClientError(Exception):
    """Base exception for client errors."""
    pass


class AuthenticationError(NashiumClientError):
    """Raised when authentication fails."""
    pass


class NoWorkAvailable(NashiumClientError):
    """Raised when no work is available (not really an error)."""
    pass


class NashiumClient:
    """
    HTTP client for the JHipster Worker API.

    Handles authentication, retries, and serialization.
    """

    def __init__(
            self,
            base_url: str,
            worker_token: str,
            timeout: float = 30.0,
            max_retries: int = 3,
    ):
        """
        Initialize the client.

        Args:
            base_url: Base URL of the JHipster server (e.g., "http://localhost:8080")
            worker_token: The X-Worker-Token for authentication
            timeout: Request timeout in seconds
            max_retries: Number of retries for failed requests
        """
        self.base_url = base_url.rstrip("/")
        self.worker_token = worker_token
        self.timeout = timeout

        self.max_retries = max_retries

        # Auto-retry only idempotent GETs. Retrying POST /claim-next after a
        # lost 200 claims a second interaction and strands the first (R18).
        # POST /result is retried explicitly below - it is keyed by
        # interactionId, so repeating it is safe.
        self.session = requests.Session()
        self.session.mount("http://", HTTPAdapter(max_retries=Retry(
            total=max_retries, backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )))
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=max_retries, backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )))

        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # Set default headers
        self.session.headers.update({
            "X-Worker-Token": self.worker_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _url(self, path: str) -> str:
        """Build full URL from path."""
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def claim_next_interaction(self) -> Optional[NextQueuedInteractionDTO]:
        """
        Claim the next queued interaction for execution.

        Returns:
            NextQueuedInteractionDTO if work is available, None otherwise.

        Raises:
            AuthenticationError: If the worker token is invalid.
            NashiumClientError: For other HTTP errors.
        """
        url = self._url("/api/worker/claim-next")

        try:
            response = self.session.post(url, timeout=self.timeout)

            if response.status_code == 204:
                # No content - no work available
                logger.debug("No work available (204 No Content)")
                return None

            if response.status_code == 401:
                raise AuthenticationError("Invalid worker token")

            response.raise_for_status()

            data = response.json()
            dto = NextQueuedInteractionDTO.model_validate(data)

            # Log any warnings from the server
            warning = response.headers.get("X-Interaction-Warning")
            if warning:
                logger.warning(f"Server warning: {warning}")

            if dto.warningMessage:
                logger.warning(f"Server warning: {dto.warningMessage}")

            return dto

        except requests.exceptions.ConnectionError as e:
            raise NashiumClientError(f"Connection failed: {e}") from e
        except requests.exceptions.Timeout as e:
            raise NashiumClientError(f"Request timed out: {e}") from e
        except requests.exceptions.HTTPError as e:
            if e.response is not None and 500 <= e.response.status_code < 600:
                raise NashiumClientError(f"HTTP error: {e}") from e
            raise

    def submit_result(self, submission: MatchResultSubmissionDTO) -> InteractionDTO:
        """
        Submit match results to the server.

        Args:
            submission: The match result data.

        Returns:
            The updated InteractionDTO.

        Raises:
            AuthenticationError: If the worker token is invalid.
            NashiumClientError: For other HTTP errors.
        """
        url = self._url("/api/worker/result")
        payload = submission.model_dump(exclude_none=True)
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)

                if response.status_code == 401:
                    raise AuthenticationError("Invalid worker token")

                if response.status_code == 409:
                    # We very likely won the race with our own earlier attempt:
                    # the server already has this result. Treat as success (R10).
                    logger.warning(
                        "Interaction %s already recorded server-side (409); "
                        "treating as submitted.", submission.interactionId)
                    try:
                        return InteractionDTO.model_validate(response.json())
                    except Exception:  # noqa: BLE001
                        return InteractionDTO(id=submission.interactionId,
                                              status="COMPLETED")

                if response.status_code == 400:
                    raise NashiumClientError(
                        f"Server rejected the payload (400): {response.text[:500]}")

                if response.status_code >= 500:
                    raise requests.exceptions.HTTPError(
                        f"Server error {response.status_code}: {response.text[:200]}")

                response.raise_for_status()
                return InteractionDTO.model_validate(response.json())

            except (AuthenticationError, NashiumClientError):
                raise
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.HTTPError) as e:
                last_error = e
                if attempt < self.max_retries:
                    backoff = 2 ** attempt
                    logger.warning("Submit failed (%s); retrying in %ss", e, backoff)
                    time.sleep(backoff)

        raise NashiumClientError(f"Failed to submit result: {last_error}") from last_error

    def health_check(self) -> bool:
        """
        Check if the server is reachable and the worker token is valid.

        Returns:
            True if server responds with 200, False otherwise.
        """
        try:
            response = self.session.get(
                self._url("/api/worker/health"),
                timeout=5.0
            )
            return response.status_code == 200
        except Exception:
            return False

    def close(self):
        """Close the client session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()