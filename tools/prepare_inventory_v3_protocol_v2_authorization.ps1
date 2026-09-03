[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')]
    [string]$OpaqueReceiptId,

    [Parameter(Mandatory = $true)]
    [string]$AttemptBase,

    [Parameter(Mandatory = $true)]
    [string]$ProposalOutput,

    [string]$DetachedCheckout
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$StartingSha = '66c7e9536539979bc60e17f02f026eb64ebf0768'
$LockSha256 = '60ff2c511e46be3b87df4e0d9e4f705d897a4181f9152f2729ee90f6c45f8cf5'
$ScriptPath = $MyInvocation.MyCommand.Path
$SourceRepository = (Resolve-Path (Join-Path (Split-Path $ScriptPath -Parent) '..')).Path

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'C8A authorization proposal must run on the actual Windows producer host.'
}

$SourceStatus = @(git -C $SourceRepository status --porcelain=v1 --untracked-files=no)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the source repository.' }
if ($SourceStatus.Count -ne 0) { throw 'The source repository has tracked changes; proposal preparation refuses to continue.' }

if ([string]::IsNullOrWhiteSpace($DetachedCheckout)) {
    $DetachedCheckout = Join-Path ([System.IO.Path]::GetTempPath()) ('Mining-Automation-C8A-L2-' + [Guid]::NewGuid().ToString('N'))
}
$DetachedCheckout = [System.IO.Path]::GetFullPath($DetachedCheckout)
$AttemptBase = [System.IO.Path]::GetFullPath($AttemptBase)
$ProposalOutput = [System.IO.Path]::GetFullPath($ProposalOutput)

foreach ($Path in @($DetachedCheckout, $AttemptBase, $ProposalOutput)) {
    if (Test-Path -LiteralPath $Path) {
        throw "Required fresh path already exists: $Path"
    }
}

$ProposalParent = Split-Path $ProposalOutput -Parent
if (-not (Test-Path -LiteralPath $ProposalParent -PathType Container)) {
    throw 'Proposal output parent must already exist.'
}
$AttemptParent = Split-Path $AttemptBase -Parent
if (-not (Test-Path -LiteralPath $AttemptParent -PathType Container)) {
    throw 'Attempt-base parent must already exist.'
}

$WorktreeAdded = $false
try {
    git -C $SourceRepository cat-file -e ($StartingSha + '^{commit}')
    if ($LASTEXITCODE -ne 0) { throw 'Exact Inventory L2 commit is unavailable locally.' }

    git -C $SourceRepository worktree add --detach $DetachedCheckout $StartingSha
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create exact detached Inventory L2 checkout.' }
    $WorktreeAdded = $true

    $ObservedHead = (git -C $DetachedCheckout rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $ObservedHead -ne $StartingSha) {
        throw 'Detached proposal checkout is not the exact Inventory L2 SHA.'
    }
    $Before = @(git -C $DetachedCheckout status --porcelain=v1 --untracked-files=all)
    if ($Before.Count -ne 0) { throw 'Exact Inventory L2 checkout is not clean.' }

    $env:C8A_REPOSITORY_ROOT = $DetachedCheckout
    $env:C8A_EXPECTED_HEAD = $StartingSha
    $env:C8A_OPAQUE_RECEIPT_ID = $OpaqueReceiptId
    $env:C8A_ATTEMPT_BASE = $AttemptBase
    $env:C8A_PROPOSAL_OUTPUT = $ProposalOutput

    @'
from __future__ import annotations

import json
import os
from pathlib import Path

from validation.inventory_v3_protocol_v2.protocol import build_live_authorization_proposal

repository = Path(os.environ["C8A_REPOSITORY_ROOT"])
attempt_base = Path(os.environ["C8A_ATTEMPT_BASE"])
proposal_output = Path(os.environ["C8A_PROPOSAL_OUTPUT"])
proposal = build_live_authorization_proposal(
    repository,
    expected_lock_head=os.environ["C8A_EXPECTED_HEAD"],
    opaque_receipt_id=os.environ["C8A_OPAQUE_RECEIPT_ID"],
    attempt_base=attempt_base,
)
expected_paths = [
    "validation/inventory_v3/live-campaign-authorizations.json",
    "validation/inventory_v3_protocol_v2/live-campaign-authorizations.json",
    "validation/inventory_v3_protocol_v2/live-campaign-authorizations.json.sha256",
]
assert proposal["status"] == "proposal-only-not-authorized"
assert proposal["activation_allowed"] is False
assert proposal["promotion_allowed"] is False
assert proposal["source_registry_modified"] is False
files = proposal["files"]
assert type(files) is list
assert [item["path"] for item in files] == expected_paths
assert not attempt_base.exists()
proposal_output.write_text(
    json.dumps(proposal, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
    newline="\n",
)
'@ | python -
    if ($LASTEXITCODE -ne 0) { throw 'Inventory C8A proposal generation failed.' }

    $After = @(git -C $DetachedCheckout status --porcelain=v1 --untracked-files=all)
    if ($After.Count -ne 0) { throw 'Proposal generation modified the exact Inventory L2 checkout.' }
    if (Test-Path -LiteralPath $AttemptBase) { throw 'Proposal generation created one-shot attempt state.' }

    $Proposal = Get-Content -LiteralPath $ProposalOutput -Raw | ConvertFrom-Json -Depth 100
    if ($Proposal.status -ne 'proposal-only-not-authorized') { throw 'Unexpected proposal status.' }
    if ($Proposal.activation_allowed -ne $false -or $Proposal.promotion_allowed -ne $false) {
        throw 'Proposal unexpectedly carries activation or promotion authority.'
    }
    if ($Proposal.source_registry_modified -ne $false) { throw 'Proposal claims a source write.' }
    $Paths = @($Proposal.files | ForEach-Object { $_.path })
    $ExpectedPaths = @(
        'validation/inventory_v3/live-campaign-authorizations.json',
        'validation/inventory_v3_protocol_v2/live-campaign-authorizations.json',
        'validation/inventory_v3_protocol_v2/live-campaign-authorizations.json.sha256'
    )
    if ((Compare-Object $Paths $ExpectedPaths).Count -ne 0) { throw 'Proposal path allowlist differs.' }

    [ordered]@{
        status = 'C8A_WINDOWS_HOST_PROPOSAL_READY_FOR_INDEPENDENT_REVIEW'
        starting_git_sha = $StartingSha
        protocol_lock_sha256 = $LockSha256
        proposal_path = $ProposalOutput
        live_pixels_captured = $false
        campaign_authorized = $false
        approval_self_granted = $false
        repository_files_changed = @()
        attempt_state_created = $false
    } | ConvertTo-Json -Depth 8
}
finally {
    Remove-Item Env:C8A_REPOSITORY_ROOT, Env:C8A_EXPECTED_HEAD, Env:C8A_OPAQUE_RECEIPT_ID, Env:C8A_ATTEMPT_BASE, Env:C8A_PROPOSAL_OUTPUT -ErrorAction SilentlyContinue
    if ($WorktreeAdded) {
        git -C $SourceRepository worktree remove --force $DetachedCheckout | Out-Null
    }
    if (Test-Path -LiteralPath $DetachedCheckout) {
        Remove-Item -LiteralPath $DetachedCheckout -Recurse -Force
    }
}
