import copy

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import UserRole
from apps.datasets.models import (
    ChangeType,
    Contributor,
    Dataset,
    DatasetProposal,
    DatasetProposalVote,
    DatasetProposalActivity,
)

from .revisions import MIN_REVIEWER_QUORUM
from .change_classification import classify_proposal


class ProposalLifecycleError(Exception):
    pass


class UnauthorizedProposalReviewerError(ProposalLifecycleError):
    pass


class DuplicateProposalVoteError(ProposalLifecycleError):
    pass


class ProposalReviewPendingError(ProposalLifecycleError):
    pass


def _reviewer_count():
    return UserRole.objects.filter(role="reviewer").values("profile_id").distinct().count()


def _is_reviewer(user):
    return UserRole.objects.filter(profile__user_id=user.pk, role="reviewer").exists()


def _can_edit_dataset(dataset, user):
    if dataset.is_owned_by(user):
        return True
    return Contributor.objects.filter(
        dataset=dataset,
        user=user,
        contributor_type__in=[
            Contributor.ContributorType.OWNER,
            Contributor.ContributorType.CO_AUTHOR,
        ],
        permission="edit",
    ).exists()


def _can_edit_proposal(dataset, proposal, user):
    if _can_edit_dataset(dataset, user):
        return True
    if proposal.source == DatasetProposal.Source.REVISION_REQUEST:
        from .revisions import has_revision_permission
        if proposal.submitted_by_id == user.pk and DatasetProposalActivity.objects.filter(
            proposal=proposal, action__in=[DatasetProposalActivity.Action.SUBMITTED, DatasetProposalActivity.Action.RESUBMITTED]
        ).exists():
            # The original revision grant is consumed on first submission; its
            # submitter may finish this already-authorized correction loop.
            return True
        return has_revision_permission(dataset, user)
    return False


def _record_activity(proposal, action, actor=None, *, from_status="", to_status="", reason="", details=None):
    return DatasetProposalActivity.objects.create(
        proposal=proposal, actor=actor, action=action,
        from_status=from_status or "", to_status=to_status or "",
        reason=(reason or "").strip(), details=details or {},
    )


def update_draft_proposal(proposal, actor, *, proposed_metadata=None, proposed_license=None,
                          license_change_reason=None, declared_change_type=None,
                          change_summary=None, release_notes=None):
    """Update owner-editable proposal content only while it is a draft."""
    from .publication import materialize_proposed_metadata, generate_license_snapshot
    with transaction.atomic():
        dataset = Dataset.objects.select_for_update().get(pk=proposal.dataset_id)
        locked = DatasetProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status != DatasetProposal.Status.DRAFT:
            raise ProposalLifecycleError("Only draft proposals can be edited.")
        if locked.submitted_by_id != actor.pk or not _can_edit_proposal(dataset, locked, actor):
            raise UnauthorizedProposalReviewerError("Only the authorized proposal submitter may edit this proposal.")
        tracked_fields = ("proposed_metadata", "proposed_license", "license_change_reason", "declared_change_type", "change_summary", "release_notes")
        before = {field: copy.deepcopy(getattr(locked, field)) for field in tracked_fields}
        if proposed_metadata is not None:
            if not isinstance(proposed_metadata, dict):
                raise ValueError("proposed_metadata must be an object.")
            materialize_proposed_metadata(locked.base_release.metadata_snapshot, proposed_metadata)
            locked.proposed_metadata = proposed_metadata
        if proposed_license is not None:
            if not isinstance(proposed_license, dict):
                raise ValueError("proposed_license must be an object.")
            generate_license_snapshot(proposed_license)
            locked.proposed_license = proposed_license
        if license_change_reason is not None:
            if not isinstance(license_change_reason, str): raise ValueError("license_change_reason must be a string.")
            locked.license_change_reason = license_change_reason.strip()
        if declared_change_type is not None:
            if declared_change_type not in ChangeType.values: raise ValueError("declared_change_type must be patch, minor, or major.")
            locked.declared_change_type = declared_change_type
        if change_summary is not None:
            if not isinstance(change_summary, str): raise ValueError("change_summary must be a string.")
            locked.change_summary = change_summary.strip()
        if release_notes is not None:
            if not isinstance(release_notes, str): raise ValueError("release_notes must be a string.")
            locked.release_notes = release_notes.strip()
        after = {field: copy.deepcopy(getattr(locked, field)) for field in tracked_fields}
        changed_fields = [field for field in tracked_fields if before[field] != after[field]]
        if not changed_fields:
            raise ValueError("No proposal content changes were supplied.")
        locked.last_activity_at = timezone.now()
        locked.save()
        _record_activity(locked, DatasetProposalActivity.Action.CONTENT_UPDATED,
                         actor, from_status=locked.status, to_status=locked.status,
                         details={"changed_fields": changed_fields,
                                  "before": {field: before[field] for field in changed_fields},
                                  "after": {field: after[field] for field in changed_fields}})
        return locked


def submit_proposal(proposal, submitted_by, computed_min_change_type=None):
    """Submit a proposal using a computed minimum derived from actual changes.

    The optional legacy argument is accepted for compatibility but ignored;
    callers cannot choose the computed minimum change type.
    """
    stale = False
    with transaction.atomic():
        dataset = Dataset.objects.select_for_update().get(pk=proposal.dataset_id)
        locked = DatasetProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status != DatasetProposal.Status.DRAFT:
            raise ProposalLifecycleError("Only draft proposals can be submitted.")
        if locked.submitted_by_id != submitted_by.pk:
            raise UnauthorizedProposalReviewerError(
                "Only the proposal submitter can submit this proposal."
            )
        has_revision_permission_grant = False
        if locked.source == DatasetProposal.Source.REVISION_REQUEST:
            from .revisions import consume_revision_permission, has_revision_permission

            has_revision_permission_grant = has_revision_permission(dataset, submitted_by)
            if not has_revision_permission_grant and DatasetProposalActivity.objects.filter(
                proposal=locked, action__in=[DatasetProposalActivity.Action.SUBMITTED, DatasetProposalActivity.Action.RESUBMITTED]
            ).exists():
                has_revision_permission_grant = True
        if not _can_edit_dataset(dataset, submitted_by) and not has_revision_permission_grant:
            raise UnauthorizedProposalReviewerError(
                "The submitter does not have edit access or approved revision permission."
            )

        if (
            dataset.published_release_id != locked.base_release_id
            or locked.base_release.status != "published"
        ):
            stale_at = timezone.now()
            DatasetProposal.objects.transition(
                locked.pk, DatasetProposal.Status.STALE, last_activity_at=stale_at,
            )
            _record_activity(locked, DatasetProposalActivity.Action.STALE, submitted_by,
                             from_status=DatasetProposal.Status.DRAFT, to_status=DatasetProposal.Status.STALE)
            stale = True
        else:
            was_resubmission = DatasetProposalActivity.objects.filter(
                proposal=locked, action=DatasetProposalActivity.Action.CHANGES_REQUESTED
            ).exists()
            # A new review round must not inherit decisions from the previous version.
            if was_resubmission:
                locked.votes.all().delete()
            classification = classify_proposal(locked)
            computed_min_change_type = classification.minimum_change_type
            submitted_at = timezone.now()
            if classification.review_required:
                DatasetProposal.objects.transition(
                    locked.pk,
                    DatasetProposal.Status.PENDING_REVIEW,
                    computed_min_change_type=computed_min_change_type,
                    submitted_at=submitted_at,
                    last_activity_at=submitted_at,
                )
            else:
                # Narrow exemption: presentation-only punctuation/spacing edits
                # to descriptive text. It is never available for files, scientific
                # metadata, or any non-PATCH change. Publish through the exact same
                # atomic release service as reviewer-approved proposals.
                DatasetProposal.objects.transition(
                    locked.pk,
                    DatasetProposal.Status.APPROVED,
                    computed_min_change_type=computed_min_change_type,
                    submitted_at=submitted_at,
                    decided_at=submitted_at,
                    review_bypassed_at=submitted_at,
                    review_bypassed_by_id=submitted_by.pk,
                    review_bypass_reason=classification.review_bypass_reason,
                    last_activity_at=submitted_at,
                )
                from .publication import publish_proposal
                publish_proposal(
                    locked,
                    published_by=submitted_by,
                    final_change_type=computed_min_change_type,
                )
                _record_activity(locked, DatasetProposalActivity.Action.REVIEW_BYPASSED, submitted_by,
                                 from_status=DatasetProposal.Status.DRAFT, to_status=DatasetProposal.Status.PUBLISHED,
                                 reason=classification.review_bypass_reason)
            if classification.review_required:
                _record_activity(locked,
                    DatasetProposalActivity.Action.RESUBMITTED if was_resubmission else DatasetProposalActivity.Action.SUBMITTED,
                    submitted_by, from_status=DatasetProposal.Status.DRAFT,
                    to_status=DatasetProposal.Status.PENDING_REVIEW,
                    details={"computed_min_change_type": computed_min_change_type})
            if locked.source == DatasetProposal.Source.REVISION_REQUEST:
                consume_revision_permission(dataset, submitted_by)
    if stale:
        raise ProposalLifecycleError(
            "The proposal base release is no longer current; the proposal is stale."
        )
    return DatasetProposal.objects.get(pk=proposal.pk)


def submit_proposal_vote(proposal, reviewer, vote, reason=""):
    if vote not in DatasetProposalVote.Vote.values:
        raise ValueError("vote must be approve or reject.")
    reason = (reason or "").strip()
    if vote == DatasetProposalVote.Vote.REJECT and not reason:
        raise ValueError("A reason is required to reject a proposal.")
    if not _is_reviewer(reviewer):
        raise UnauthorizedProposalReviewerError(
            "Only users with the reviewer role may vote on proposals."
        )

    with transaction.atomic():
        proposal_dataset_id = DatasetProposal.objects.values_list(
            "dataset_id", flat=True
        ).get(pk=proposal.pk)
        Dataset.objects.select_for_update().get(pk=proposal_dataset_id)
        locked = DatasetProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status != DatasetProposal.Status.PENDING_REVIEW:
            raise ProposalLifecycleError("This proposal is not awaiting review.")
        if locked.submitted_by_id == reviewer.pk:
            raise UnauthorizedProposalReviewerError("A proposal submitter cannot review their own proposal.")
        try:
            with transaction.atomic():
                vote_row = DatasetProposalVote.objects.create(
                    proposal=locked,
                    reviewer=reviewer,
                    vote=vote,
                    reason=reason,
                )
                _record_activity(locked, DatasetProposalActivity.Action.VOTE_CAST, reviewer,
                                 from_status=locked.status, to_status=locked.status,
                                 reason=reason, details={"vote": vote, "vote_id": str(vote_row.pk)})
        except IntegrityError as exc:
            raise DuplicateProposalVoteError(
                "A reviewer may submit only one decision per proposal."
            ) from exc

        reviewer_count = _reviewer_count()
        quorum = min(MIN_REVIEWER_QUORUM, reviewer_count) or 1
        eligible_votes = locked.votes.filter(
            reviewer__profile__roles__role="reviewer"
        )
        approvals = eligible_votes.filter(
            vote=DatasetProposalVote.Vote.APPROVE
        ).count()
        rejections = eligible_votes.filter(
            vote=DatasetProposalVote.Vote.REJECT
        ).count()
        votes_cast = approvals + rejections

        decision = None
        if votes_cast >= quorum:
            if approvals > rejections:
                decision = DatasetProposal.Status.APPROVED
            elif rejections > approvals:
                decision = DatasetProposal.Status.REJECTED

        if decision:
            decided_at = timezone.now()
            DatasetProposal.objects.transition(
                locked.pk, decision, decided_at=decided_at, last_activity_at=decided_at,
            )
            _record_activity(locked,
                DatasetProposalActivity.Action.APPROVED if decision == DatasetProposal.Status.APPROVED else DatasetProposalActivity.Action.REJECTED,
                reviewer, from_status=DatasetProposal.Status.PENDING_REVIEW,
                to_status=decision, details={"approve_votes": approvals, "reject_votes": rejections, "quorum": quorum})
        else:
            locked.last_activity_at = timezone.now()
            locked.save(update_fields=["last_activity_at"])

        return {
            "status": decision or DatasetProposal.Status.PENDING_REVIEW,
            "approve_votes": approvals,
            "reject_votes": rejections,
            "quorum": quorum,
        }


def request_proposal_changes(proposal, reviewer, reason):
    """Move a pending proposal back to its submitter with mandatory feedback."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A clear explanation is required when requesting changes.")
    if not _is_reviewer(reviewer):
        raise UnauthorizedProposalReviewerError("Only a reviewer may request proposal changes.")
    with transaction.atomic():
        dataset_id = DatasetProposal.objects.values_list("dataset_id", flat=True).get(pk=proposal.pk)
        Dataset.objects.select_for_update().get(pk=dataset_id)
        locked = DatasetProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status != DatasetProposal.Status.PENDING_REVIEW:
            raise ProposalLifecycleError("Only proposals awaiting review can be sent back for changes.")
        if locked.submitted_by_id == reviewer.pk:
            raise UnauthorizedProposalReviewerError("A proposal submitter cannot request changes to their own proposal.")
        now = timezone.now()
        # Write feedback first so the PostgreSQL lifecycle trigger can require
        # a persisted, non-empty reviewer explanation for this transition.
        _record_activity(locked, DatasetProposalActivity.Action.CHANGES_REQUESTED, reviewer,
                         from_status=DatasetProposal.Status.PENDING_REVIEW,
                         to_status=DatasetProposal.Status.CHANGES_REQUESTED, reason=reason)
        DatasetProposal.objects.transition(
            locked.pk, DatasetProposal.Status.CHANGES_REQUESTED,
            decided_at=now, last_activity_at=now,
        )
        return DatasetProposal.objects.get(pk=locked.pk)


def resubmit_requested_proposal(proposal, actor):
    """Re-open a changes-requested proposal as a clean editable draft."""
    with transaction.atomic():
        dataset = Dataset.objects.select_for_update().get(pk=proposal.dataset_id)
        locked = DatasetProposal.objects.select_for_update().get(pk=proposal.pk)
        if locked.status != DatasetProposal.Status.CHANGES_REQUESTED:
            raise ProposalLifecycleError("Only proposals with requested changes can be resubmitted for editing.")
        if locked.submitted_by_id != actor.pk or not _can_edit_proposal(dataset, locked, actor):
            raise UnauthorizedProposalReviewerError("Only the authorized submitter may revise this proposal.")
        now = timezone.now()
        DatasetProposal.objects.transition(
            locked.pk, DatasetProposal.Status.DRAFT,
            submitted_at=None, decided_at=None, computed_min_change_type=None,
            final_change_type=None, review_bypassed_at=None,
            review_bypassed_by_id=None, review_bypass_reason="",
            last_activity_at=now,
        )
        # Preserve old votes in immutable activity events, but invalidate them
        # before any owner edits can change the reviewed content.
        locked.votes.all().delete()
        _record_activity(locked, DatasetProposalActivity.Action.REOPENED_FOR_REVISION, actor,
                         from_status=DatasetProposal.Status.CHANGES_REQUESTED,
                         to_status=DatasetProposal.Status.DRAFT)
        return DatasetProposal.objects.get(pk=locked.pk)


def cancel_proposal(proposal, actor, reason=""):
    if reason is not None and not isinstance(reason, str):
        raise ValueError("Cancellation reason must be a string.")
    reason = (reason or "").strip()
    with transaction.atomic():
        dataset = Dataset.objects.select_for_update().get(pk=proposal.dataset_id)
        locked = DatasetProposal.objects.select_for_update().get(pk=proposal.pk)
        if actor.pk != locked.submitted_by_id and not _can_edit_dataset(dataset, actor):
            raise UnauthorizedProposalReviewerError(
                "Only the submitter or dataset owner may cancel this proposal."
            )
        if locked.status not in {
            DatasetProposal.Status.DRAFT,
            DatasetProposal.Status.PENDING_REVIEW,
            DatasetProposal.Status.CHANGES_REQUESTED,
            DatasetProposal.Status.APPROVED,
        }:
            raise ProposalLifecycleError("This proposal can no longer be cancelled.")
        now = timezone.now()
        old_status = locked.status
        DatasetProposal.objects.transition(
            locked.pk, DatasetProposal.Status.CANCELLED,
            decided_at=now, last_activity_at=now,
        )
        _record_activity(locked, DatasetProposalActivity.Action.CANCELLED, actor,
                         from_status=old_status, to_status=DatasetProposal.Status.CANCELLED,
                         reason=reason)
        return DatasetProposal.objects.get(pk=locked.pk)
