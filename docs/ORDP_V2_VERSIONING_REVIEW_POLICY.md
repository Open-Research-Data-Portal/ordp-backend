# ORDP V2 Dataset Versioning and Review Policy

**Scope:** dataset release versioning, metadata/file revisions, proposal review, revision requests, publication integrity, and DOI/PID release integration. License administration and license-versioning are excluded; those belong to the licensing owner. A release may preserve a license snapshot where existing architecture requires it, but this policy does not define license editing or approval.

## 1. Immutable release rule

A published release is an immutable record. It contains a descriptive metadata snapshot, citation snapshot, and immutable file manifest. A correction or change to published metadata or file content must not rewrite an existing release. Publishing an accepted change creates exactly one new release for the complete proposal. A no-op proposal must not create a release.

The initial V2 release is `1.0.0` when no trustworthy SemVer history exists. Existing V1 history is retained and must not be fabricated into historical SemVer releases.

## 2. SemVer classification

The server classifies the complete diff; clients cannot dictate the resulting version. A reviewer may raise, but not lower, the minimum severity established by the server. If a proposal contains multiple changes, the highest applicable severity wins.

- **PATCH:** typo/format correction or data correction that does not alter intended meaning, compatibility, or interpretation.
- **MINOR:** backward-compatible addition, such as compatible metadata or appended observations.
- **MAJOR:** breaking or meaning-changing change, including removing important fields/records, changing units, methodology, scope, or the population represented.

A percentage-difference measure is a review signal only, not the SemVer classifier. Exact field-level classification should be implemented deterministically and tested; ambiguous scientific changes default to review and conservative severity.

## 3. Review routing

| Change class | Default route |
|---|---|
| Low-risk descriptive typo/format correction | May publish without reviewer approval, as PATCH, with audit trail |
| Small descriptive correction that does not alter meaning | May publish without review, as PATCH, with audit trail |
| Metadata affecting interpretation, coverage, methodology, attribution, or dataset scope | Reviewer approval required |
| Compatible data addition | MINOR; reviewer approval required by default |
| Data-value correction | PATCH only when meaning is unchanged; review required by default |
| File addition/replacement/removal | Reviewer approval required by default; classify the whole manifest change |
| Breaking/meaning-changing change | MAJOR and reviewer approval required |
| Visibility, access, archive, embargo, or workflow state | Operational change: audit separately, not a semantic release by itself |

No-review publication is not an in-place mutation. It must use the same snapshot, manifest, integrity, transaction, and idempotency safeguards as reviewed publication.

## 4. Revision initiation and review

1. Dataset owner or authorized editor may create a proposal containing metadata changes, file changes, or both.
2. Another user may submit a revision request. This request does not modify the dataset. The owner decides whether to accept it and create/update a proposal.
3. A reviewer may approve, reject (with reason), or request changes (with mandatory explanation). Requesting changes moves the proposal to `changes_requested`; the submitter reopens it as a draft, edits it, and submits a fresh review round.
4. Reviewer comments, votes, decisions, actor, and timestamps remain in the append-only proposal activity history across resubmissions. Votes from an earlier review round are cleared from the active vote set when the revised proposal is resubmitted.
5. Rejected, cancelled, stale, or unsubmitted proposals are never published.
6. Only the owner/authorized editor changes proposal content. Reviewers do not edit metadata or files directly.

## 5. Proposal and publication consistency

- A proposal records the exact base release.
- A dataset may have only one active proposal unless conflict handling is explicitly added.
- Submission and publication lock the dataset/proposal in a consistent order and verify the base release remains current.
- If the base release is no longer current, the proposal becomes stale and must be reconciled/rebased; it cannot silently publish over the newer release.
- Publication is idempotent. A retry must not create duplicate release rows, version identifiers, file manifest entries, or DOI registration work.
- One proposal containing N metadata fields and M file changes creates at most one release.

## 6. File integrity

Stored objects are immutable and addressed by stable object identifiers. A replacement creates a new object; the old release continues to reference its original object. Each release manifest records logical file identity, object identity, and verified SHA-256. Cross-dataset object references are forbidden. File-only changes create a release with the prior metadata snapshot; metadata-only changes create a release with the prior manifest.

## 7. DOI/PID behavior

Internal PIDs and external DOIs are distinct. Support concept-level and release-level DOI relationships using structured metadata (`isVersionOf` / `hasVersion`) rather than parsing DOI suffixes. DOI registration is asynchronous and retryable; provider failure does not roll back a published release. Never report a DOI as registered until the provider confirms it. Withdrawal preserves the release and identifier history. Provider credentials and institutional registration are deployment configuration, not guaranteed by the code.

## 8. Audit and tests

Record proposal creation, submission, reviewer decisions, change requests, owner resubmission, publication, withdrawal, revision requests, and operational state changes. Test metadata-only, file-only, combined, multi-field, no-op, review-bypass eligibility, reviewer-requested edits, external revision request, stale base, concurrent publication, idempotent retry, object integrity, and DOI retry scenarios. Run Django checks, migration checks, and PostgreSQL-backed tests before claiming production readiness.
