# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the Order Workflow State Machine (seller-awh)."""

import pytest

from ad_seller.models.order_state_machine import (
    InvalidTransitionError,
    OrderAuditLog,
    OrderStateMachine,
    OrderStatus,
    TransitionRule,
    from_execution_order_status,
    from_execution_status,
)

# =============================================================================
# OrderStatus enum
# =============================================================================


class TestOrderStatus:
    def test_all_expected_statuses_exist(self):
        expected = {
            "draft",
            "submitted",
            "pending_approval",
            "approved",
            "rejected",
            "in_progress",
            "syncing",
            "completed",
            "failed",
            "cancelled",
            "booked",
            "unbooked",
        }
        actual = {s.value for s in OrderStatus}
        assert actual == expected

    def test_string_serialization(self):
        assert OrderStatus.DRAFT == "draft"
        assert str(OrderStatus.BOOKED) == "OrderStatus.BOOKED"


# =============================================================================
# Happy-path transitions
# =============================================================================


class TestHappyPath:
    def test_full_lifecycle_draft_to_completed(self):
        sm = OrderStateMachine(order_id="order-001")
        assert sm.status == OrderStatus.DRAFT

        sm.transition(OrderStatus.SUBMITTED, actor="agent:seller")
        sm.transition(OrderStatus.APPROVED, actor="system")
        sm.transition(OrderStatus.IN_PROGRESS, actor="system")
        sm.transition(OrderStatus.SYNCING, actor="system")
        sm.transition(OrderStatus.BOOKED, actor="ad_server:freewheel")
        sm.transition(OrderStatus.COMPLETED, actor="system")

        assert sm.status == OrderStatus.COMPLETED
        assert len(sm.history) == 6

    def test_approval_gate_path(self):
        sm = OrderStateMachine(order_id="order-002")
        sm.transition(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.PENDING_APPROVAL)
        sm.transition(OrderStatus.APPROVED, actor="human:ops-lead")

        assert sm.status == OrderStatus.APPROVED
        # Check the approval transition record
        approval = sm.history[-1]
        assert approval.from_status == OrderStatus.PENDING_APPROVAL
        assert approval.to_status == OrderStatus.APPROVED
        assert approval.actor == "human:ops-lead"

    def test_rejection_and_revision_path(self):
        sm = OrderStateMachine(order_id="order-003")
        sm.transition(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.PENDING_APPROVAL)
        sm.transition(OrderStatus.REJECTED, actor="human:manager", reason="Rate too low")

        # Return to draft for revision
        sm.transition(OrderStatus.DRAFT, reason="Revising terms")
        assert sm.status == OrderStatus.DRAFT
        assert len(sm.history) == 4


# =============================================================================
# Invalid transitions
# =============================================================================


class TestInvalidTransitions:
    def test_cannot_skip_states(self):
        sm = OrderStateMachine(order_id="order-010")
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(OrderStatus.COMPLETED)
        assert "draft" in str(exc_info.value)
        assert "completed" in str(exc_info.value)

    def test_cannot_go_backwards_arbitrarily(self):
        sm = OrderStateMachine(order_id="order-011")
        sm.transition(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.APPROVED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(OrderStatus.SUBMITTED)

    def test_completed_is_terminal(self):
        sm = OrderStateMachine(order_id="order-012")
        sm.transition(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.APPROVED)
        sm.transition(OrderStatus.IN_PROGRESS)
        sm.transition(OrderStatus.SYNCING)
        sm.transition(OrderStatus.BOOKED)
        sm.transition(OrderStatus.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(OrderStatus.DRAFT)

    def test_error_includes_order_id(self):
        sm = OrderStateMachine(order_id="order-err")
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(OrderStatus.BOOKED)
        assert "order-err" in str(exc_info.value)


# =============================================================================
# Guard conditions
# =============================================================================


class TestGuardConditions:
    def test_guard_allows_transition(self):
        def require_high_value(order_id, from_s, to_s, ctx):
            return ctx.get("total_value", 0) > 1000

        rule = TransitionRule(
            from_status=OrderStatus.SUBMITTED,
            to_status=OrderStatus.APPROVED,
            guard=require_high_value,
            description="Auto-approve high-value orders",
        )

        sm = OrderStateMachine(order_id="order-020", rules=[rule])
        sm._status = OrderStatus.SUBMITTED  # set directly for isolated test

        assert sm.can_transition(OrderStatus.APPROVED, {"total_value": 5000})
        sm.transition(OrderStatus.APPROVED, context={"total_value": 5000})
        assert sm.status == OrderStatus.APPROVED

    def test_guard_blocks_transition(self):
        def require_high_value(order_id, from_s, to_s, ctx):
            return ctx.get("total_value", 0) > 1000

        rule = TransitionRule(
            from_status=OrderStatus.SUBMITTED,
            to_status=OrderStatus.APPROVED,
            guard=require_high_value,
        )

        sm = OrderStateMachine(order_id="order-021", rules=[rule])
        sm._status = OrderStatus.SUBMITTED

        assert not sm.can_transition(OrderStatus.APPROVED, {"total_value": 500})
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(OrderStatus.APPROVED, context={"total_value": 500})
        assert "guard condition failed" in str(exc_info.value)


# =============================================================================
# Allowed transitions query
# =============================================================================


class TestAllowedTransitions:
    def test_draft_can_submit_or_cancel(self):
        sm = OrderStateMachine(order_id="order-030")
        allowed = sm.allowed_transitions()
        assert OrderStatus.SUBMITTED in allowed
        assert OrderStatus.CANCELLED in allowed
        assert OrderStatus.COMPLETED not in allowed

    def test_submitted_has_multiple_paths(self):
        sm = OrderStateMachine(order_id="order-031")
        sm.transition(OrderStatus.SUBMITTED)
        allowed = sm.allowed_transitions()
        assert OrderStatus.PENDING_APPROVAL in allowed
        assert OrderStatus.APPROVED in allowed
        assert OrderStatus.CANCELLED in allowed
        assert OrderStatus.FAILED in allowed


# =============================================================================
# Audit log
# =============================================================================


class TestAuditLog:
    def test_transitions_recorded_in_order(self):
        sm = OrderStateMachine(order_id="order-040")
        sm.transition(OrderStatus.SUBMITTED, actor="agent:buyer")
        sm.transition(OrderStatus.APPROVED, actor="system")

        log = sm.audit_log
        assert log.order_id == "order-040"
        assert len(log.transitions) == 2
        assert log.transitions[0].to_status == OrderStatus.SUBMITTED
        assert log.transitions[1].to_status == OrderStatus.APPROVED
        assert log.current_status == OrderStatus.APPROVED

    def test_transition_metadata_preserved(self):
        sm = OrderStateMachine(order_id="order-041")
        sm.transition(
            OrderStatus.SUBMITTED,
            actor="human:ops",
            reason="Rush order",
            metadata={"priority": "high", "campaign_id": "camp-123"},
        )

        record = sm.history[0]
        assert record.actor == "human:ops"
        assert record.reason == "Rush order"
        assert record.metadata["priority"] == "high"

    def test_empty_audit_log_has_no_current_status(self):
        log = OrderAuditLog(order_id="order-empty")
        assert log.current_status is None


# =============================================================================
# Custom rules
# =============================================================================


class TestCustomRules:
    def test_add_custom_rule(self):
        sm = OrderStateMachine(order_id="order-050")
        sm.transition(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.APPROVED)
        sm.transition(OrderStatus.IN_PROGRESS)
        sm.transition(OrderStatus.SYNCING)
        sm.transition(OrderStatus.BOOKED)
        sm.transition(OrderStatus.COMPLETED)

        # Default: completed has no outgoing transitions
        assert sm.allowed_transitions() == []

        # Add a custom rule allowing completed -> draft (re-open)
        sm.add_rule(
            TransitionRule(
                from_status=OrderStatus.COMPLETED,
                to_status=OrderStatus.DRAFT,
                description="Re-open completed order",
            )
        )
        assert OrderStatus.DRAFT in sm.allowed_transitions()

    def test_remove_rule(self):
        sm = OrderStateMachine(order_id="order-051")
        assert OrderStatus.CANCELLED in sm.allowed_transitions()

        removed = sm.remove_rule(OrderStatus.DRAFT, OrderStatus.CANCELLED)
        assert removed is True
        assert OrderStatus.CANCELLED not in sm.allowed_transitions()

    def test_remove_nonexistent_rule(self):
        sm = OrderStateMachine(order_id="order-052")
        removed = sm.remove_rule(OrderStatus.COMPLETED, OrderStatus.BOOKED)
        assert removed is False


# =============================================================================
# Serialization
# =============================================================================


class TestSerialization:
    def test_round_trip_serialization(self):
        sm = OrderStateMachine(order_id="order-060")
        sm.transition(OrderStatus.SUBMITTED, actor="agent:buyer")
        sm.transition(OrderStatus.APPROVED, actor="system")

        data = sm.to_dict()
        assert data["order_id"] == "order-060"
        assert data["status"] == "approved"

        restored = OrderStateMachine.from_dict(data)
        assert restored.order_id == "order-060"
        assert restored.status == OrderStatus.APPROVED
        assert len(restored.history) == 2
        assert restored.history[0].actor == "agent:buyer"

    def test_from_dict_with_empty_audit(self):
        data = {"order_id": "order-061", "status": "draft"}
        sm = OrderStateMachine.from_dict(data)
        assert sm.status == OrderStatus.DRAFT
        assert len(sm.history) == 0


# =============================================================================
# Legacy enum mapping
# =============================================================================


class TestLegacyMapping:
    def test_execution_status_mapping(self):
        assert from_execution_status("initialized") == OrderStatus.DRAFT
        assert from_execution_status("pending_approval") == OrderStatus.PENDING_APPROVAL
        assert from_execution_status("accepted") == OrderStatus.APPROVED
        assert from_execution_status("syncing_to_ad_server") == OrderStatus.SYNCING
        assert from_execution_status("completed") == OrderStatus.COMPLETED
        assert from_execution_status("failed") == OrderStatus.FAILED

    def test_execution_order_status_mapping(self):
        assert from_execution_order_status("draft") == OrderStatus.DRAFT
        assert from_execution_order_status("proposed") == OrderStatus.SUBMITTED
        assert from_execution_order_status("booked") == OrderStatus.BOOKED
        assert from_execution_order_status("unbooked") == OrderStatus.UNBOOKED
        assert from_execution_order_status("canceled") == OrderStatus.CANCELLED

    def test_unknown_status_defaults_to_draft(self):
        assert from_execution_status("unknown_value") == OrderStatus.DRAFT
        assert from_execution_order_status("unknown_value") == OrderStatus.DRAFT


# =============================================================================
# Cancellation from multiple states
# =============================================================================


class TestCancellation:
    @pytest.mark.parametrize(
        "intermediate",
        [
            OrderStatus.SUBMITTED,
            OrderStatus.PENDING_APPROVAL,
            OrderStatus.APPROVED,
            OrderStatus.IN_PROGRESS,
        ],
    )
    def test_cancel_from_active_states(self, intermediate):
        sm = OrderStateMachine(order_id="order-070")
        # Get to the intermediate state
        if intermediate == OrderStatus.SUBMITTED:
            sm.transition(OrderStatus.SUBMITTED)
        elif intermediate == OrderStatus.PENDING_APPROVAL:
            sm.transition(OrderStatus.SUBMITTED)
            sm.transition(OrderStatus.PENDING_APPROVAL)
        elif intermediate == OrderStatus.APPROVED:
            sm.transition(OrderStatus.SUBMITTED)
            sm.transition(OrderStatus.APPROVED)
        elif intermediate == OrderStatus.IN_PROGRESS:
            sm.transition(OrderStatus.SUBMITTED)
            sm.transition(OrderStatus.APPROVED)
            sm.transition(OrderStatus.IN_PROGRESS)

        sm.transition(OrderStatus.CANCELLED, actor="human:ops", reason="Client pulled out")
        assert sm.status == OrderStatus.CANCELLED

    def test_cancel_from_draft(self):
        sm = OrderStateMachine(order_id="order-071")
        sm.transition(OrderStatus.CANCELLED)
        assert sm.status == OrderStatus.CANCELLED


# =============================================================================
# Task 9: Expanded tests
# =============================================================================


class TestInvalidTransitionErrorMessages:
    """Invalid transition raises error with clear message."""

    def test_error_message_includes_from_and_to_states(self):
        sm = OrderStateMachine(order_id="order-100")
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(OrderStatus.COMPLETED)
        msg = str(exc_info.value)
        assert "draft" in msg
        assert "completed" in msg
        assert "order-100" in msg

    def test_error_message_includes_reason(self):
        sm = OrderStateMachine(order_id="order-101")
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(OrderStatus.BOOKED)
        msg = str(exc_info.value)
        assert "no matching transition rule" in msg

    def test_error_attributes_accessible(self):
        sm = OrderStateMachine(order_id="order-102")
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(OrderStatus.COMPLETED)
        err = exc_info.value
        assert err.order_id == "order-102"
        assert err.from_status == OrderStatus.DRAFT
        assert err.to_status == OrderStatus.COMPLETED


class TestGuardConditionBlocking:
    """Guard condition failure blocks transition with clear error."""

    def test_guard_failure_preserves_original_state(self):
        def always_reject(order_id, from_s, to_s, ctx):
            return False

        rule = TransitionRule(
            from_status=OrderStatus.SUBMITTED,
            to_status=OrderStatus.APPROVED,
            guard=always_reject,
            description="Always blocks",
        )
        sm = OrderStateMachine(order_id="order-110", rules=[rule])
        sm._status = OrderStatus.SUBMITTED

        with pytest.raises(InvalidTransitionError):
            sm.transition(OrderStatus.APPROVED)

        # State should remain unchanged after guard failure
        assert sm.status == OrderStatus.SUBMITTED
        assert len(sm.history) == 0

    def test_guard_with_context_based_condition(self):
        def require_approval_level(order_id, from_s, to_s, ctx):
            return ctx.get("approval_level", 0) >= 2

        rule = TransitionRule(
            from_status=OrderStatus.SUBMITTED,
            to_status=OrderStatus.APPROVED,
            guard=require_approval_level,
        )
        sm = OrderStateMachine(order_id="order-111", rules=[rule])
        sm._status = OrderStatus.SUBMITTED

        # Should fail with insufficient level
        assert not sm.can_transition(OrderStatus.APPROVED, {"approval_level": 1})

        # Should pass with sufficient level
        assert sm.can_transition(OrderStatus.APPROVED, {"approval_level": 2})


class TestAuditLogDetails:
    """Audit log records every transition with actor and timestamp."""

    def test_each_transition_has_timestamp(self):
        sm = OrderStateMachine(order_id="order-120")
        sm.transition(OrderStatus.SUBMITTED, actor="agent:buyer")
        sm.transition(OrderStatus.APPROVED, actor="human:ops")

        for t in sm.history:
            assert t.timestamp is not None
            # Timestamps should be datetime objects
            from datetime import datetime

            assert isinstance(t.timestamp, datetime)

    def test_each_transition_has_actor(self):
        sm = OrderStateMachine(order_id="order-121")
        sm.transition(OrderStatus.SUBMITTED, actor="agent:buyer")
        sm.transition(OrderStatus.APPROVED, actor="human:ops-lead")

        assert sm.history[0].actor == "agent:buyer"
        assert sm.history[1].actor == "human:ops-lead"

    def test_default_actor_is_system(self):
        sm = OrderStateMachine(order_id="order-122")
        sm.transition(OrderStatus.SUBMITTED)

        assert sm.history[0].actor == "system"

    def test_transitions_recorded_chronologically(self):
        sm = OrderStateMachine(order_id="order-123")
        sm.transition(OrderStatus.SUBMITTED, actor="a1")
        sm.transition(OrderStatus.APPROVED, actor="a2")
        sm.transition(OrderStatus.IN_PROGRESS, actor="a3")

        for i in range(1, len(sm.history)):
            assert sm.history[i].timestamp >= sm.history[i - 1].timestamp

    def test_transition_id_is_unique(self):
        sm = OrderStateMachine(order_id="order-124")
        sm.transition(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.APPROVED)
        sm.transition(OrderStatus.IN_PROGRESS)

        ids = [t.transition_id for t in sm.history]
        assert len(ids) == len(set(ids))  # all unique


class TestTerminalStateBlocking:
    """Transition from terminal state (completed, failed) is blocked."""

    def test_completed_blocks_all_transitions(self):
        sm = OrderStateMachine(order_id="order-130")
        sm.transition(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.APPROVED)
        sm.transition(OrderStatus.IN_PROGRESS)
        sm.transition(OrderStatus.SYNCING)
        sm.transition(OrderStatus.BOOKED)
        sm.transition(OrderStatus.COMPLETED)

        assert sm.allowed_transitions() == []

        for target in [
            OrderStatus.DRAFT,
            OrderStatus.SUBMITTED,
            OrderStatus.APPROVED,
            OrderStatus.IN_PROGRESS,
        ]:
            with pytest.raises(InvalidTransitionError):
                sm.transition(target)

    def test_failed_allows_only_draft_reset(self):
        sm = OrderStateMachine(order_id="order-131")
        sm.transition(OrderStatus.SUBMITTED)
        sm.transition(OrderStatus.FAILED)

        # Failed can go back to draft (defined in default rules)
        allowed = sm.allowed_transitions()
        assert OrderStatus.DRAFT in allowed

        # But cannot go to other states
        assert OrderStatus.COMPLETED not in allowed
        assert OrderStatus.APPROVED not in allowed

        # Can actually transition to draft
        sm.transition(OrderStatus.DRAFT, reason="Retry after fix")
        assert sm.status == OrderStatus.DRAFT

    def test_cancelled_is_terminal(self):
        sm = OrderStateMachine(order_id="order-132")
        sm.transition(OrderStatus.CANCELLED)

        assert sm.allowed_transitions() == []
        with pytest.raises(InvalidTransitionError):
            sm.transition(OrderStatus.DRAFT)
