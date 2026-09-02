# Inventory V3 independent-validation Protocol V2

This is the non-activating execution/finalization successor to frozen Protocol V1. It wraps the single frozen V3 candidate and evaluator; it does not change the classifier, prototypes, constants, thresholds, or expected UNKNOWN behavior.

Frozen identities:

- V3 candidate F: `5975532b472a74d93f010e04ca44b2efa2a3ffd7`
- Protocol V1 source P: `b3b141e0d9ca15d729eaa98c795f6c855bff68cf`
- Protocol V1 lock L: `32764bfd82afb46d4e99292bab7d162be536e2d7`
- Protocol V1 lock SHA-256: `64ab45f8b0294f733c4517ad46ebb01e722f3fbf3d14d52feb79649b5a3649f1`
- Passive capture configuration: `inventory-positive-v3-independent-passive-natural-fill-v1`
- Publication floor: `0.8`

Protocol V2's exact P2/L2 identities are read from `protocol-lock.json` after the reviewed lock-only L2 commit. Every command below requires the caller to supply exact current Git HEAD and independently verifies ordinary F → P → L → P2 → L2 ancestry, the exact add-only P2 path set, clean full history, no replace refs/grafts, the exact preregistration sidecar, exact locked blobs, and authorization ancestry. The isolated launcher rejects committed or ignored namespace/package/native/sourceless competitors before local imports and keeps the standard library ahead of the repository root.

Threat model: this protocol assumes one trusted Windows user on one trusted host and a repository-controlled process. Actor strings are auditable assertions, not cryptographic authentication; opaque UUID uniqueness is the trusted source owner's responsibility; reservations are per Windows user rather than host-global. It rejects ordinary stale, colliding, tampered, replaced, or misordered state, but does not claim protection from a hostile administrator/other account, concurrent hostile post-check filesystem replacement, PKI absence, or non-WORM storage. Those limits do not relax frozen compatibility, exact lineage, lifecycle gates, path budgets, closed trees, privacy, or non-activation requirements.

The P2/L2 closure also binds the frozen development manifest and its sidecar. Before any campaign pixel or reviewer truth is opened, metadata-only preflight verifies those exact bytes and rejects reuse of the development dataset, session, case, or capture identities. Pixel equality alone is not an identity collision: the frozen evaluator may disclose `byte_identical_to_development_payload=true`, and that disclosure remains valid evidence accounting when all source-owned identities are disjoint.

## A. Preflight before any live action

In PowerShell, from the exact repository worktree:

```powershell
$InventoryRepo = (git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Repository-root verification failed; stop' }
$InventoryHead = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'HEAD verification failed; stop' }
$InventoryPython = Join-Path $InventoryRepo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $InventoryPython -PathType Leaf)) { throw 'Verified repository Python >=3.12 is unavailable' }
& $InventoryPython -c "import sys; assert sys.version_info >= (3, 12); print(sys.executable); print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'Python verification failed; stop' }
git status --short --branch
if ($LASTEXITCODE -ne 0) { throw 'Git status failed; stop' }
git replace -l
if ($LASTEXITCODE -ne 0) { throw 'Replace-ref verification failed; stop' }
git rev-parse --is-shallow-repository
if ($LASTEXITCODE -ne 0) { throw 'Shallow-history verification failed; stop' }
git show -s --format=%P 32764bfd82afb46d4e99292bab7d162be536e2d7
if ($LASTEXITCODE -ne 0) { throw 'Frozen history verification failed; stop' }
```

Before authorization, `$InventoryHead` must be exact reviewed L2. The trusted source owner is the uniqueness authority for one newly issued canonical random UUIDv4; it must not have been used for another authorization and contains no campaign, pixel, truth, time, or hash material. Generate the read-only exact proposal/readiness result:

```powershell
& $InventoryPython -I -S "$InventoryRepo\validation\inventory_v3_protocol_v2\launcher.py" authorization-proposal --expected-head $InventoryHead --opaque-receipt-id <PREISSUED-UUIDV4>
if ($LASTEXITCODE -ne 0) { throw 'Authorization readiness failed; stop' }
```

The proposal fails unless the per-Windows-user legacy reservation and all fixed source/workspace/result/attempt paths are unused, every fixed path fits Windows with long-path support disabled, approval remains absent, the OS-observed producer identity is obtainable, and both authorization registries are canonical empty. This reservation is explicitly per Windows user, not host-global. The proposal output is deterministic for exact L2 plus the preissued UUID and performs no source write.

## B. Future source-owned live authorization

The proposal is not authorization and the coordinator never applies it. A distinct source owner must review and commit exactly the three proposed files in one ordinary direct descendant of L2:

- `validation/inventory-positive-v3/live-campaign-authorizations.json`
- `validation/inventory_v3_protocol_v2/live-campaign-authorizations.json`
- `validation/inventory_v3_protocol_v2/live-campaign-authorizations.json.sha256`

The single non-merge commit must contain no other path, must be a direct child of exact L2, and must be later than L2. The legacy entry binds exact P/L/V1-lock/config; the V2 entry binds exact F/P2/L2/V2-lock/config and the preissued opaque receipt. Its authorization ID is recomputed from that exact content. The legacy registry's only change after frozen V1 L must be this same commit. Live execution HEAD must remain exactly this authorization commit; no later commit is eligible. After that source action only:

```powershell
$InventoryHead = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Authorized HEAD verification failed; stop' }
git diff-tree --no-commit-id --name-only -r $InventoryHead
if ($LASTEXITCODE -ne 0) { throw 'Authorization commit inspection failed; stop' }
$InventoryAuthorizationRegistry = Get-Content -Raw -LiteralPath "$InventoryRepo\validation\inventory_v3_protocol_v2\live-campaign-authorizations.json" | ConvertFrom-Json
if ($InventoryAuthorizationRegistry.authorizations.Count -ne 1) { throw 'Exact V2 authorization is unavailable; stop' }
$InventoryAuthorizationId = [string]$InventoryAuthorizationRegistry.authorizations[0].authorization_id
if ($InventoryAuthorizationId -notmatch '^[0-9a-f]{64}$') { throw 'Authorization identity differs; stop' }
```

Operator, independent reviewer, and proposed approver must be three distinct actor identities. Source authorization does not approve conformance, production binding, promotion, or activation.

## C. Seven-case passive capture

Only after explicit source authorization, run the isolated V2 launcher once. It invokes the exact frozen V1 passive launcher as a child process and adds only the V2 producer attestation after the frozen completion seal:

```powershell
& $InventoryPython -I -S "$InventoryRepo\validation\inventory_v3_protocol_v2\launcher.py" capture --expected-head $InventoryHead --operator <OPERATOR> --runelite-build <EXACT-BUILD> --client-mode <MODE> --theme <THEME> --renderer <RENDERER>
if ($LASTEXITCODE -ne 0) { throw 'Passive campaign failed permanently; stop' }
```

The immutable operator-acknowledged sequence is:

1. `empty` — reviewer truth must ultimately establish 0 occupied slots.
2. `early-partial` — natural ordinary-iron fill.
3. `mid-partial` — natural ordinary-iron fill later than early.
4. `near-full` — natural ordinary-iron fill below 28.
5. `full` — 28 occupied slots.
6. `wrong-tab` — reviewer truth must establish UNKNOWN/no count.
7. `row-obstruction` — reviewer truth must establish UNKNOWN/no count.

No detector chooses, retries, drops, replaces, or advances a capture. All owned captures are retained. Do not manipulate bank/item fixtures, arbitrarily rearrange inventory, automate input, or use any result to select evidence. A capture/tool failure consumes the one-shot reservation and remains terminal.

Finalize every owned capture in source order with no caller-selected path or case:

```powershell
& $InventoryPython -I -S "$InventoryRepo\validation\inventory_v3_protocol_v2\launcher.py" finalize --expected-head $InventoryHead
if ($LASTEXITCODE -ne 0) { throw 'Acquisition finalization failed permanently; stop' }
```

The two frozen V1 components use incompatible campaign-ID encodings. The locked V2 finalizer therefore keeps every original source file byte-for-byte and creates two separately named evaluator-compatibility metadata documents. The bridge changes only the derived session `campaign_id`, and the derived seal `campaign_id` plus its derived-session hash. Original and derived hashes, the fixed bridge ID, and the closed acquisition tree are all bound before review; captured pixels, reports, order, labels, and evaluator code are unchanged. The recorded finalization time is the actual wall-clock observation and must be strictly later than source completion.

## D. Independent review

Only after acquisition finalization, create the blinded review package:

```powershell
& $InventoryPython -I -S "$InventoryRepo\validation\inventory_v3_protocol_v2\launcher.py" prepare-review --expected-head $InventoryHead
if ($LASTEXITCODE -ne 0) { throw 'Reviewer intake preparation failed permanently; stop' }
```

The intake renames cases and evidence paths opaquely. It contains no capture ID, case ID, stage, operator identity, operator label, or prefilled truth. A distinct reviewer inspects the fixed evidence and records truth through the package-owned prompt, not by inventing JSON:

The prompt prints exact absolute paths to headerless BGRA evidence. In a second
PowerShell window, define this read-only Windows viewer once; it holds the bytes
in memory and writes no derivative into either evidence or repository trees.
`Bgr32` deliberately treats each BGRA pixel's fourth byte as unused, matching
the frozen evaluator and avoiding GDI's undefined alpha byte changing what the
reviewer can see:

```powershell
Add-Type -AssemblyName PresentationCore,PresentationFramework
function Show-InventoryBgra([string]$Path, [int]$Width, [int]$Height) {
    $Bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($Bytes.Length -ne ($Width * $Height * 4)) { throw 'BGRA evidence length differs' }
    $Bitmap = [System.Windows.Media.Imaging.BitmapSource]::Create(
        $Width, $Height, 96, 96,
        [System.Windows.Media.PixelFormats]::Bgr32,
        $null, $Bytes, ($Width * 4)
    )
    $Image = [System.Windows.Controls.Image]::new()
    $Image.Source = $Bitmap
    $Image.Stretch = [System.Windows.Media.Stretch]::Uniform
    $Window = [System.Windows.Window]::new()
    $Window.Title = [System.IO.Path]::GetFileName($Path)
    $Window.Content = $Image
    $Window.Width = [Math]::Min(($Width + 40), 1200)
    $Window.Height = [Math]::Min(($Height + 70), 900)
    $null = $Window.ShowDialog()
}
```

For every blind prompt, inspect both printed paths before answering:

```powershell
Show-InventoryBgra '<PRINTED-ABSOLUTE-FULL-FRAME-PATH>' 1005 1078
Show-InventoryBgra '<PRINTED-ABSOLUTE-INVENTORY-REGION-PATH>' 158 248
```

```powershell
& $InventoryPython -I -S "$InventoryRepo\validation\inventory_v3_protocol_v2\launcher.py" record-review --expected-head $InventoryHead --reviewer <INDEPENDENT-REVIEWER>
if ($LASTEXITCODE -ne 0) { throw 'Independent review submission failed permanently; stop' }
& $InventoryPython -I -S "$InventoryRepo\validation\inventory_v3_protocol_v2\launcher.py" publish-review --expected-head $InventoryHead
if ($LASTEXITCODE -ne 0) { throw 'Reviewed-package publication failed permanently; stop' }
```

Review truth is opened/collected only after repository, lock, authorization, per-user reservation, source session, chronology, disjoint-root, prior successful-operation ledger, and closed-tree metadata preflight. The review timestamp must be observed inside that one interactive collection call and strictly after finalization; predated or future truth is rejected. A rejected/ineligible case is not replaced. Every reserved capture/finalize/intake/submission/publish operation ends in either a closed success package or a private failure binding plus privacy-safe public failure receipt; any failure permanently blocks later stages.

## E. Locked evaluator

Run the terminal evaluator once:

```powershell
& $InventoryPython -I -S "$InventoryRepo\validation\inventory_v3_protocol_v2\launcher.py" evaluate --expected-head $InventoryHead
if ($LASTEXITCODE -ne 0) { throw 'Locked evaluator execution failed permanently; stop' }
$InventoryResultPath = "$InventoryRepo\diagnostics\iv3v2r\$InventoryAuthorizationId\protocol-v2-terminal-result.json"
if (-not (Test-Path -LiteralPath $InventoryResultPath -PathType Leaf)) { throw 'Terminal evaluator record is unavailable; stop' }
$InventoryTerminal = Get-Content -Raw -LiteralPath $InventoryResultPath | ConvertFrom-Json
if ($InventoryTerminal.terminal_status -eq 'conformance-failed-permanent' -or $InventoryTerminal.detector_conformance_passed -ne $true) {
    throw 'Inventory V3 detector conformance is release FAIL; stop and do not request approval'
}
if (
    $InventoryTerminal.terminal_status -ne 'conformance-passed-source-approval-required' -or
    $InventoryTerminal.contract_id -ne 'CONFORMANCE_PASSED_APPROVAL_REQUIRED' -or
    $InventoryTerminal.authorization_id -ne $InventoryAuthorizationId -or
    $InventoryTerminal.approval_required -ne $true -or
    $InventoryTerminal.retry_allowed -ne $false -or
    $InventoryTerminal.activation_allowed -ne $false -or
    $InventoryTerminal.promotion_allowed -ne $false
) { throw 'Terminal PASS interpretation differs; stop' }
```

Evaluator exit 0 means only that one immutable terminal result was published. It
does not mean detector PASS; the exact record checks above are mandatory before
the approval-request command is eligible.

The coordinator reserves the evaluator attempt before reviewer truth or validation pixels are opened, fully verifies the exact reviewed-package tree, then calls only `evaluate_frozen_v3_independent_validation` from the frozen V1 blob. It rechecks the complete reviewed-package snapshot after evaluation and before publishing a result, including V2-only/original bridge members the frozen evaluator does not consume. PASS, FAIL, or tool/integrity failure is terminal for this authorization/package/protocol identity. A second invocation cannot replace the first result.

Only a terminal detector-conformance PASS may produce a non-authoritative approval request. A third actor supplies the proposed source-review identity and a UTC time strictly later than both independent review and evaluation and not later than the request's actual wall-clock observation:

```powershell
& $InventoryPython -I -S "$InventoryRepo\validation\inventory_v3_protocol_v2\launcher.py" approval-request --expected-head $InventoryHead --proposed-approver <DISTINCT-APPROVER> --proposed-approved-at-utc <UTC-Z-TIMESTAMP>
if ($LASTEXITCODE -ne 0) { throw 'Approval-request preparation failed permanently; stop' }
```

This command snapshots the approval registry before/after and fails if it changes. It emits the exact proposed frozen-registry JSON and sidecar bytes but never writes them to the source registry. A later distinct source action and later production-binding change remain required.

## F. Exact release interpretation

- `conformance-passed-source-approval-required`: detector conformance passed, but release is still blocked. `activation_allowed=false` and `promotion_allowed=false`. A distinct source approval and a later reviewed production binding remain required.
- `conformance-failed-permanent`: release FAIL. The first failed seven-case contract is recorded privately, a privacy-safe public receipt carries only the opaque receipt, failure contract, terminal class, and false authority flags, and retry is forbidden.
- Attempt terminal `failed-terminal` with a preregistered failure contract: release FAIL. Its private record is `failed-terminal-permanent`; partial/owned evidence remains retained and cannot be hidden, replaced, or treated as a clean retry. If failure packaging itself is interrupted, the irrevocable reservation's declared fallback remains `ATTEMPT_INTEGRITY_FAILURE` and no later stage may advance.
- A source approval request cannot reinterpret detector FAIL as PASS. Missing, stale, wrong, rebound, or premature approval remains non-activating.
- Validation pixels/truth are private, ignored, and ineligible for prototypes, training, calibration, model construction, or post-campaign V3 tuning.

`LIVE INVENTORY CAMPAIGN NOT YET AUTHORIZED` until the exact reviewed source authorization commit exists. Protocol V2 never activates WorldState, controllers, navigation, resource perception, banking, or input.
