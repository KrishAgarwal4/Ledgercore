import hashlib
import json
from typing import Optional, Tuple, Dict, Any
import asyncpg
from ledgercore.exceptions import (
    IdempotencyConflictError,
    IdempotencyPayloadMismatchError,
)


def compute_request_hash(method: str, path: str, body: Any) -> str:
    """
    Computes a canonical SHA-256 hash of the HTTP method, path, and JSON-encoded payload.
    """
    hasher = hashlib.sha256()
    hasher.update(method.upper().encode("utf-8"))
    hasher.update(b":")
    hasher.update(path.encode("utf-8"))
    hasher.update(b":")
    
    if body is not None:
        if isinstance(body, bytes):
            hasher.update(body)
        elif isinstance(body, str):
            hasher.update(body.encode("utf-8"))
        else:
            canonical_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
            hasher.update(canonical_json.encode("utf-8"))

    return hasher.hexdigest()


class IdempotencyService:
    @staticmethod
    async def process_or_reserve_key(
        conn: asyncpg.Connection,
        key: str,
        request_hash: str
    ) -> Optional[Tuple[int, Dict[str, Any]]]:
        """
        Attempts to reserve an idempotency key.
        - If new: creates record with status 'PROCESSING' and returns None (proceed with execution).
        - If previously 'COMPLETED' with matching hash: returns cached (status_code, response_body).
        - If previously 'COMPLETED' with different hash: raises IdempotencyPayloadMismatchError.
        - If currently 'PROCESSING': raises IdempotencyConflictError.
        """
        # Attempt atomic insert
        inserted = await conn.fetchval(
            """
            INSERT INTO idempotency_records (idempotency_key, request_hash, status, created_at, updated_at)
            VALUES ($1, $2, 'PROCESSING', NOW(), NOW())
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING idempotency_key;
            """,
            key,
            request_hash,
        )

        if inserted:
            # Key successfully reserved for processing
            return None

        # Key already exists: inspect current state
        row = await conn.fetchrow(
            """
            SELECT request_hash, status, response_code, response_body
            FROM idempotency_records
            WHERE idempotency_key = $1;
            """,
            key,
        )

        if not row:
            # Concurrency edge case, proceed
            return None

        # Check hash match
        if row["request_hash"] != request_hash:
            raise IdempotencyPayloadMismatchError(key)

        status = row["status"]
        if status == "PROCESSING":
            raise IdempotencyConflictError(key)
        elif status == "COMPLETED":
            response_body = row["response_body"]
            if isinstance(response_body, str):
                response_body = json.loads(response_body)
            return (row["response_code"], response_body)
        elif status == "FAILED":
            # Previous attempt failed, allow retry by updating status back to PROCESSING
            await conn.execute(
                """
                UPDATE idempotency_records
                SET status = 'PROCESSING', request_hash = $2, updated_at = NOW()
                WHERE idempotency_key = $1;
                """,
                key,
                request_hash,
            )
            return None

        return None

    @staticmethod
    async def record_success(
        conn: asyncpg.Connection,
        key: str,
        status_code: int,
        response_body: Dict[str, Any]
    ) -> None:
        """
        Finalizes an idempotency record upon successful transaction commit.
        """
        body_json = json.dumps(response_body, default=str)
        await conn.execute(
            """
            UPDATE idempotency_records
            SET status = 'COMPLETED', response_code = $2, response_body = $3::jsonb, updated_at = NOW()
            WHERE idempotency_key = $1;
            """,
            key,
            status_code,
            body_json,
        )

    @staticmethod
    async def record_failure(
        conn: asyncpg.Connection,
        key: str
    ) -> None:
        """
        Marks an idempotency record as FAILED or cleans it up so clients can retry.
        """
        await conn.execute(
            """
            UPDATE idempotency_records
            SET status = 'FAILED', updated_at = NOW()
            WHERE idempotency_key = $1;
            """,
            key,
        )
