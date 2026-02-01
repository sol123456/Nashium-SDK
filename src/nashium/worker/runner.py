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

        # Configure session with retries
        self.session = requests.Session()

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )

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
            raise NashiumClientError(f"HTTP error: {e}") from e

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

        try:
            # Convert to JSON, excluding None values
            payload = submission.model_dump(exclude_none=True)

            logger.debug(f"Submitting result for interaction {submission.interactionId}")

            response = self.session.post(url, json=payload, timeout=self.timeout)

            if response.status_code == 401:
                raise AuthenticationError("Invalid worker token")

            if response.status_code == 400:
                raise NashiumClientError(f"Bad request: {response.text}")

            if response.status_code == 409:
                raise NashiumClientError(f"Conflict: Interaction state changed")

            response.raise_for_status()

            data = response.json()
            return InteractionDTO.model_validate(data)

        except requests.exceptions.ConnectionError as e:
            raise NashiumClientError(f"Connection failed: {e}") from e
        except requests.exceptions.Timeout as e:
            raise NashiumClientError(f"Request timed out: {e}") from e
        except requests.exceptions.HTTPError as e:
            raise NashiumClientError(f"HTTP error: {e}") from e

    def health_check(self) -> bool:
        """
        Check if the server is reachable.

        Returns:
            True if server responds, False otherwise.
        """
        try:
            # Try to claim (will either succeed or return 204)
            response = self.session.post(
                self._url("/api/worker/claim-next"),
                timeout=5.0
            )
            return response.status_code in (200, 204, 401)
        except Exception:
            return False

    def close(self):
        """Close the client session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()