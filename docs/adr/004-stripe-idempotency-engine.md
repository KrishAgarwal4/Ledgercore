# ADR 004: Stripe-Compatible Idempotency Key Specification & Request Hashing

## Context
In distributed financial networks, network timeouts, mobile packet loss, and automatic client retries frequently cause identical charge or transfer requests to be submitted multiple times. Without idempotency controls, this results in catastrophic double-billing or multiple transfers.

A naive unique index on `idempotency_key` is inadequate:
1. It does not detect if a client accidentally reuses a key with a *different* transaction payload (silent payload substitution attack or client programming error).
2. It fails to handle in-flight concurrent duplicate requests cleanly, often producing database unique constraint crashes rather than coordinated conflict responses.

## Decision
LedgerCore implements a **Stripe-grade Idempotency Engine** conforming to the industry standard:

### 1. Payload Fingerprinting
A cryptographic SHA-256 fingerprint is calculated across the HTTP method, request path, and canonicalized JSON payload:
$$\text{Request Hash} = \text{SHA256}(\text{Method} \mathbin{\Vert} \text{Path} \mathbin{\Vert} \text{CanonicalJSON}(\text{Body}))$$

### 2. State Machine & Atomic Key Reservation
Keys are tracked in `idempotency_records` through three explicit states:
- `PROCESSING`: An active worker holds the lock and is currently executing the transfer.
- `COMPLETED`: The transaction committed successfully, with `response_code` and `response_body` serialized and stored.
- `FAILED`: Execution terminated with an unhandled error; the key is released to permit legitimate retries.

### 3. Protocol Invariants
| Condition | Engine Behavior | HTTP Code |
| :--- | :--- | :--- |
| First submission of key | Key atomically reserved with status `PROCESSING`; proceeds to transaction execution. | 201 Created |
| Retry while original is still processing | Concurrent duplicate detected; caller advised to wait or back off. | 409 Conflict |
| Retry with matching key and matching payload | Exact serialized response returned with `X-Idempotent-Replay: true`. Zero duplicate database writes. | 201 / 200 Cached |
| Reuse of key with altered payload | Mismatch detected against original request hash; execution aborted. | 422 Unprocessable Entity |

## Verification
Automated pytest suites (`test_idempotent_replay_returns_cached_response` and `test_idempotency_payload_mismatch_rejected`) verify that 50 concurrent requests with the identical key post exactly 1 ledger entry, and altered payloads are strictly rejected.
