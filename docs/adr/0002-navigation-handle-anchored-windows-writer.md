# ADR 0002: Windows handle-anchored navigation evidence writer

- Status: Proposed
- Date: 2026-09-01

## Context

ADR 0001 intentionally narrowed the offline pathname writer to a trusted, non-hostile dedicated
parent. `Path.open("x")` resolves the parent again, so a parent/root replacement between the last
identity check and exclusive create can receive bytes. Root identity in the terminal manifest
prevents a complete replacement clone from verifying, but it cannot prevent the misdirected
write. The pathname implementation is therefore permanently marked
`DURABLE_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE=false`.

Future direction-specific route evidence needs a writer that owns the object receiving every
create. This is a navigation evidence boundary, not a generic artifact store, input boundary, or
route-release decision.

## Decision

### Windows is the only supported fresh-directory platform

The supported backend is Windows `win32`. It uses native `NtCreateFile` because `CreateFileW`
cannot create a child relative to an existing directory handle.

The existing parent must reside on the explicitly reviewed fixed local NTFS envelope. Drive type
is checked as `DRIVE_FIXED` and the filesystem is queried through the retained drive-root handle as
`NTFS` before root creation; mapped/network drives, UNC paths, other filesystems, and reparse-point
ancestry are unsupported. The parent is opened with backup-semantics/open-reparse-point flags and
checked as a directory with a stable, nonzero `FILE_ID_INFO`, no delete-pending state, and no
reparse attribute/tag. All ancestry is checked before that handle is opened, and the path/native
identity must agree before the first mutation.

The drive root is opened once and each existing ancestry component is opened relative to the
previous retained directory handle with open-reparse-point behavior. Directory handles request
only the access needed for identity queries and child creation and remain asynchronous for ACL
compatibility. The transaction root is then created with one portable component, the held parent
as `OBJECT_ATTRIBUTES.RootDirectory`, `FILE_CREATE`, `FILE_DIRECTORY_FILE`, and write-through
directory semantics. The returned handle is the proof of the object created by that call. The
same one-component, handle-relative operation creates every owned directory and file; file
handles retain synchronous write-through semantics. `OPEN_IF`, overwrite, adoption, and pathname
fallback do not exist.

The public contract and review-gated eligibility values are:

```text
windows_nt_handle_relative_no_follow_fresh_directory_v1
HANDLE_ANCHORED_WRITER_PROCESS_INTEGRITY_REQUIRED = True
HANDLE_ANCHORED_WRITER_FUTURE_REAL_EVIDENCE_ELIGIBLE = False
```

Platform support is not sufficient to flip the release-facing eligibility value. It remains false
pending lead review of the explicit process-integrity boundary. Each concrete parent must also
pass the fixed-local-NTFS and native capability checks. Missing APIs and unsupported parent paths
fail before root reservation. A check that can only be applied to the atomically created root
handle, such as its stable file ID, can fail after an empty owned root exists; that failure still
precedes sequencer/source access and evidence bytes.

### Retained ownership and immutable writes

Parent, root, owned-directory, and completed-file handles remain owned until a transaction stops
or finalizes. Each retained handle is marked `HANDLE_FLAG_PROTECT_FROM_CLOSE` before it enters the
ledger, and protection is rechecked before native identity use. An ordinary competing
`CloseHandle` cannot invalidate a just-validated parent and reuse the same integer to redirect the
next relative create. File creation is exclusive. The created handle is duplicated into a CRT
descriptor for a complete write, `fsync`, rewind, bounded readback, and same-handle identity check;
the original handle remains retained. Later validation reopens the exact one-component name
relative to the retained parent and compares native volume/file ID, kind, reparse state, link
count, size, and the stable path identity used by the strict reader.

Every create is preceded and followed by full owned-graph validation. The exact tree is checked
before and after the terminal manifest. The terminal manifest remains the last successful write.
An invalidated handle poisons the transaction; the code never reconstructs a mutable capability
from a saved pathname.

After the terminal write, while the owned handles are still retained, the writer computes a
schema-tagged SHA-256 over the root and every expected directory/file physical identity. The
identity uses stable mode, device, inode/file ID, link count, file attributes, and reparse tag and
includes the terminal manifest itself; volatile timestamps and content fields are excluded. The
required digest is returned only in the caller-owned acquisition/review receipt. Strict intake
recomputes it from the exact tree before accepting content, so exact-byte file replacement and
cloned child-directory replacement fail even when all content digests still match. Receipt
construction follows the pin and precedes handle cleanup; failure in either step returns no
receipt.

The review plan cross-binds the acquisition physical identity. Its persisted schema is therefore
`fixed-route-durable-review-plan-v2`; the required-field addition is not represented as v1.

### Failure, concurrency, and cleanup

Two writers racing for the same parent/name use native `FILE_CREATE`; exactly one can own the
fresh root and the other receives a collision. A preclaimed file, directory, or reparse name is
never adopted.

A failure keeps the created prefix as nonreviewable audit evidence. Cleanup revalidates native
type and physical identity plus the retained close-protection bit before closing each numeric
handle. It clears that bit only for the controlled close and visits the full ledger in reverse
depth at most once. An unprotected stale numeric value, including a new handle to the same owned
file, is reported but never closed. Arbitrary code executing inside the writer process could
deliberately clear protection or mutate private ledger state and is outside this filesystem
integrity boundary. The writer intentionally exposes no pathname deletion or rollback operation:
an identity-check-then-delete sequence would introduce another foreign-deletion race. Foreign
files and aliases are never removed.

The reviewed user-mode `NtCreateFile` / `OBJECT_ATTRIBUTES` surface exposes no supported
create-time close-protection flag. The returned handle is therefore protected immediately while
it is an unpublished local, before identity queries or ledger publication. The process-integrity
prerequisite excludes code already running inside the writer process that intercepts that native
return and rewrites process handles; the ordinary external filesystem race model cannot access a
process-local handle. The source-owned future-real gate remains false until the lead accepts this
boundary.

A same-principal actor with metadata-write authority can create a hard-link alias in the interval
between exclusive file creation and post-write link-count validation. The alias can then observe
the bytes written through the owned handle. Post-write validation detects the additional link,
latches STOP, and prevents a receipt/finalized package; cleanup never deletes the alias. This
writer provides evidence integrity and fail-closed authority, not confidentiality against another
principal already authorized to mutate the transaction filesystem.

An acknowledgement failure after a terminal create can leave a terminal-named owned file with no
returned receipt, as with the prior transaction contract. Strict intake and external pins remain
required; directory existence and terminal filename presence are never authority.

### Linux fails before effects

No Linux fresh-directory backend is claimed. `mkdirat()` returns success/failure rather than the
created directory's file descriptor. An attacker can replace that name before `openat()`; the
opened inode may be the replacement, and later inode checks cannot recover which inode
`mkdirat()` created. `openat2()` resolves an existing directory but does not create a directory and
return its handle atomically.

Accordingly, non-Windows factories fail before root creation, sequencer construction, source or
detector identity access, clock access, or evidence bytes. Supporting Linux would require a new
authority boundary such as a separately trusted pre-opened root, or a different single-file
transaction format, and a new ADR.

### Eligibility is not release authority

The handle-anchored factories currently drive the same synthetic, offline acquisition/review
state machines. All emitted evidence still states the architecture-test role, and
`live_navigation_enabled`, `activation_allowed`, and `input_authority` remain false. No route,
checkpoint, mine, bank, endpoint state, controller, WorldState, or input is activated.

A later reviewed campaign export must bind that its acquisition and review roots were issued by
this writer. Merely importing the eligibility constant, passing a synthetic verification report,
or copying bytes from the pathname writer cannot establish future real evidence.

## Consequences

Under the stated process-integrity prerequisite, ordinary external filesystem replacement cannot
redirect Windows creates by swapping a previously validated pathname; every child targets the
retained physical directory. Native identities, exclusive creation, handle
invalidation, hard-link contamination, reparse substitution, partial failure, competing writers,
and parent/root replacement are deterministic regression surfaces.

The implementation is intentionally platform-specific and uses a private `ntdll` boundary. Linux
CI proves fail-before-effects behavior and typing/import safety. The dedicated Windows navigation
writer workflow runs the full durable-evidence file on `windows-latest` and proves the actual ABI,
fixed-local-NTFS gate, create, flush/readback, replacement, collision, review, and cleanup behavior.

This does not prove storage-controller/power-loss durability, defend against kernel compromise,
grant route-release authority, collect real route evidence, or authorize live input.
