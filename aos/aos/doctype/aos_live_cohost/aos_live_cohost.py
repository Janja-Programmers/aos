# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import (
    add_to_date,
    get_datetime,
    now_datetime,
)
from aos.api.live.constants import (
    LIVE_COHOST_MAX_ACTIVE_SLOTS,
    LIVE_COHOST_REQUEST_EXPIRY_SECONDS,
)


LIVE_COHOST_DOCTYPE = "AOS Live CoHost"
LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_VIEW_DOCTYPE = "AOS Live Stream View"
USER_DOCTYPE = "User"

LIVE_STATUS = "live"
ENDED_LIVE_STATUS = "ended"

HOST_INVITE = "host_invite"
VIEWER_REQUEST = "viewer_request"

PENDING_STATUS = "pending"
ACCEPTED_STATUS = "accepted"
REJECTED_STATUS = "rejected"
CANCELLED_STATUS = "cancelled"
ACTIVE_STATUS = "active"
ENDED_STATUS = "ended"
EXPIRED_STATUS = "expired"

VALID_REQUEST_TYPES = {
    HOST_INVITE,
    VIEWER_REQUEST,
}

VALID_STATUSES = {
    PENDING_STATUS,
    ACCEPTED_STATUS,
    REJECTED_STATUS,
    CANCELLED_STATUS,
    ACTIVE_STATUS,
    ENDED_STATUS,
    EXPIRED_STATUS,
}

TERMINAL_STATUSES = {
    REJECTED_STATUS,
    CANCELLED_STATUS,
    ENDED_STATUS,
    EXPIRED_STATUS,
}

UNRESOLVED_STATUSES = {
    PENDING_STATUS,
    ACCEPTED_STATUS,
    ACTIVE_STATUS,
}

SLOT_RESERVED_STATUSES = {
    ACCEPTED_STATUS,
    ACTIVE_STATUS,
}

VALID_END_REASONS = {
    "left",
    "removed_by_host",
    "live_ended",
    "disconnected",
    "timeout",
    "error",
}

VALID_STATUS_TRANSITIONS = {
    PENDING_STATUS: {
        ACCEPTED_STATUS,
        REJECTED_STATUS,
        CANCELLED_STATUS,
        EXPIRED_STATUS,
    },
    ACCEPTED_STATUS: {
        ACTIVE_STATUS,
        CANCELLED_STATUS,
        EXPIRED_STATUS,
    },
    ACTIVE_STATUS: {
        ENDED_STATUS,
    },
    REJECTED_STATUS: set(),
    CANCELLED_STATUS: set(),
    ENDED_STATUS: set(),
    EXPIRED_STATUS: set(),
}

IMMUTABLE_FIELDS = {
    "live_stream",
    "user",
    "session_id",
    "request_type",
    "requested_by",
    "requested_at",
}


class AOSLiveCoHost(Document):
    def before_validate(self):
        self._normalize_values()
        self._set_initial_values()
        self._expire_pending_request_if_needed()
        self._apply_status_side_effects()
        self._set_derived_values()

    def validate(self):
        self._validate_required_fields()
        self._validate_request_type()
        self._validate_status()
        self._validate_immutable_fields()
        self._validate_live_stream()
        self._validate_cohost_user()
        self._validate_requested_by()
        self._validate_view_session()
        self._validate_status_transition()
        self._validate_no_duplicate_workflow()
        self._validate_cohost_slot_limit()
        self._validate_response_fields()
        self._validate_livekit_identity()
        self._validate_active_state()
        self._validate_end_state()
        self._validate_timestamps()
        self._validate_metadata()

    def on_trash(self):
        """
        Allow permanent administrative deletion only after the live ends.

        Runtime APIs use status transitions and soft lifecycle states.
        System Managers may permanently clear historical co-host records
        through Desk after the parent live stream has ended.
        """
        live_status = frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            self.live_stream,
            "status",
        )

        if live_status != ENDED_LIVE_STATUS:
            frappe.throw(
                "Co-host records can only be permanently deleted "
                "after the live has ended."
            )

    # NORMALIZATION
    def _normalize_values(self):
        self.request_type = str(
            self.request_type or ""
        ).strip().lower()

        self.status = str(
            self.status or PENDING_STATUS
        ).strip().lower()

        self.session_id = str(
            self.session_id or ""
        ).strip()

        if self.end_reason:
            self.end_reason = str(
                self.end_reason
            ).strip().lower()

        if self.response_reason is not None:
            self.response_reason = (
                str(
                    self.response_reason
                ).strip()
                or None
            )

    def _set_initial_values(self):
        if not self.is_new():
            return

        now = now_datetime()

        self.status = PENDING_STATUS
        self.is_active = 0

        if not self.requested_at:
            self.requested_at = now

        if not self.expires_at:
            self.expires_at = add_to_date(
                now,
                seconds=(
                    LIVE_COHOST_REQUEST_EXPIRY_SECONDS
                ),
                as_datetime=True,
            )

        live = self._get_live()

        if not self.requested_by:
            if self.request_type == HOST_INVITE:
                self.requested_by = (
                    live.host_user
                    if live
                    else None
                )

            elif self.request_type == VIEWER_REQUEST:
                self.requested_by = self.user

    def _expire_pending_request_if_needed(self):
        """
        Convert an expired pending workflow when it is next saved.

        A scheduled cleanup job may later expire records that are never
        fetched or otherwise saved after expiration.
        """
        if self.status != PENDING_STATUS:
            return

        if not self.expires_at:
            return

        if (
            get_datetime(
                self.expires_at
            )
            > now_datetime()
        ):
            return

        self.status = EXPIRED_STATUS
        self.is_active = 0

    def _apply_status_side_effects(self):
        previous_status = (
            self._get_previous_status()
        )

        if self.is_new():
            previous_status = None

        if previous_status == self.status:
            return

        now = now_datetime()

        if self.status == ACCEPTED_STATUS:
            if not self.responded_at:
                self.responded_at = now

            if not self.accepted_at:
                self.accepted_at = now

            if not self.responded_by:
                self.responded_by = (
                    self._expected_responder()
                )

        elif self.status == REJECTED_STATUS:
            if not self.responded_at:
                self.responded_at = now

            if not self.responded_by:
                self.responded_by = (
                    self._expected_responder()
                )

        elif self.status == CANCELLED_STATUS:
            if not self.responded_at:
                self.responded_at = now

            if not self.responded_by:
                self.responded_by = (
                    self.requested_by
                )

        elif self.status == ACTIVE_STATUS:
            if not self.started_at:
                self.started_at = now

        elif self.status == ENDED_STATUS:
            if not self.ended_at:
                self.ended_at = now

            self._set_default_ended_by()

    def _set_derived_values(self):
        self.is_active = int(
            self.status == ACTIVE_STATUS
        )

        if self.user and self.session_id:
            self.livekit_identity = (
                f"user:{self.user}:"
                f"session:{self.session_id}"
            )
        else:
            self.livekit_identity = None

    # VALIDATIONS
    def _validate_required_fields(self):
        required_fields = {
            "live_stream": "Live stream",
            "user": "Co-host user",
            "session_id": "Viewer session ID",
            "request_type": "Request type",
            "status": "Status",
            "requested_by": "Requested by",
            "requested_at": "Requested at",
        }

        for fieldname, label in (
            required_fields.items()
        ):
            if self.get(fieldname):
                continue

            frappe.throw(
                f"{label} is required."
            )

    def _validate_request_type(self):
        if (
            self.request_type
            not in VALID_REQUEST_TYPES
        ):
            frappe.throw(
                f"Invalid co-host request type: "
                f"{self.request_type}."
            )

    def _validate_status(self):
        if self.status not in VALID_STATUSES:
            frappe.throw(
                f"Invalid co-host status: "
                f"{self.status}."
            )

        if (
            self.is_new()
            and self.status != PENDING_STATUS
        ):
            frappe.throw(
                "A new co-host request must start as pending."
            )

    def _validate_immutable_fields(self):
        if self.is_new():
            return

        previous = self.get_doc_before_save()

        if not previous:
            return

        for fieldname in IMMUTABLE_FIELDS:
            old_value = previous.get(
                fieldname
            )
            new_value = self.get(
                fieldname
            )

            if old_value == new_value:
                continue

            label = (
                self.meta.get_label(
                    fieldname
                )
                or fieldname.replace(
                    "_",
                    " ",
                ).title()
            )

            frappe.throw(
                f"{label} cannot be changed after "
                f"the co-host request is created."
            )

    def _validate_live_stream(self):
        live = self._get_live()

        if not live:
            frappe.throw(
                "Invalid live stream."
            )

        if self.is_new():
            if (
                live.status != LIVE_STATUS
                or not bool(
                    live.is_active
                )
            ):
                frappe.throw(
                    "Cannot create a co-host request "
                    "for an inactive live stream."
                )

            return

        # Accepting or activating requires a currently active live.
        #
        # Cancellation and ending remain valid while the live is being
        # closed, including the cleanup performed by end_live.
        if self.status in {
            ACCEPTED_STATUS,
            ACTIVE_STATUS,
        }:
            if (
                live.status != LIVE_STATUS
                or not bool(
                    live.is_active
                )
            ):
                frappe.throw(
                    "The live stream must be active "
                    "to accept or activate a co-host."
                )

    def _validate_cohost_user(self):
        user = frappe.db.get_value(
            USER_DOCTYPE,
            self.user,
            [
                "name",
                "enabled",
            ],
            as_dict=True,
        )

        if not user:
            frappe.throw(
                "Invalid co-host user."
            )

        if not bool(user.enabled):
            frappe.throw(
                "Co-host user account is disabled."
            )

        live = self._get_live()

        if (
            live
            and live.host_user == self.user
        ):
            frappe.throw(
                "The live host cannot become "
                "their own co-host."
            )

    def _validate_requested_by(self):
        live = self._get_live()

        if not live:
            return

        if self.request_type == HOST_INVITE:
            if (
                self.requested_by
                != live.host_user
            ):
                frappe.throw(
                    "A host invitation must be requested "
                    "by the live host."
                )

        elif self.request_type == VIEWER_REQUEST:
            if self.requested_by != self.user:
                frappe.throw(
                    "A viewer request must be requested "
                    "by the co-host user."
                )

    def _validate_view_session(self):
        """
        Require an active viewer session while the workflow is unresolved.

        Required when:
        - the request is created
        - the workflow remains pending
        - the workflow is accepted
        - co-hosting becomes active

        Terminal transitions remain possible after the viewer session closes.
        """
        must_have_active_session = bool(
            self.is_new()
            or self.status in {
                PENDING_STATUS,
                ACCEPTED_STATUS,
                ACTIVE_STATUS,
            }
        )

        if not must_have_active_session:
            return

        view = frappe.db.get_value(
            LIVE_VIEW_DOCTYPE,
            {
                "live_stream": (
                    self.live_stream
                ),
                "user": self.user,
                "session_id": (
                    self.session_id
                ),
                "is_active": 1,
            },
            [
                "name",
                "user",
                "session_id",
            ],
            as_dict=True,
        )

        if not view:
            frappe.throw(
                "The co-host user must have an active "
                "viewer session for this live stream."
            )

    def _validate_status_transition(self):
        if self.is_new():
            return

        previous_status = (
            self._get_previous_status()
        )

        if not previous_status:
            return

        if previous_status == self.status:
            return

        allowed = (
            VALID_STATUS_TRANSITIONS.get(
                previous_status,
                set(),
            )
        )

        if self.status not in allowed:
            frappe.throw(
                f"Invalid co-host status transition: "
                f"{previous_status} → {self.status}."
            )

    def _validate_no_duplicate_workflow(self):
        """
        Prevent multiple unresolved records for the same candidate and live.
        """
        if (
            self.status
            not in UNRESOLVED_STATUSES
        ):
            return

        existing = frappe.db.exists(
            LIVE_COHOST_DOCTYPE,
            {
                "live_stream": (
                    self.live_stream
                ),
                "user": self.user,
                "status": [
                    "in",
                    list(
                        UNRESOLVED_STATUSES
                    ),
                ],
                "name": [
                    "!=",
                    self.name or "",
                ],
            },
        )

        if existing:
            frappe.throw(
                "This viewer already has an unresolved "
                "co-host request for the live stream."
            )

    def _validate_cohost_slot_limit(self):
        """
        Reserve a co-host slot when the workflow is accepted or active.

        Pending workflows do not consume co-host slots.
        """
        if (
            self.status
            not in SLOT_RESERVED_STATUSES
        ):
            return

        reserved_count = frappe.db.count(
            LIVE_COHOST_DOCTYPE,
            filters={
                "live_stream": (
                    self.live_stream
                ),
                "status": [
                    "in",
                    list(
                        SLOT_RESERVED_STATUSES
                    ),
                ],
                "name": [
                    "!=",
                    self.name or "",
                ],
            },
        )

        if (
            int(
                reserved_count or 0
            )
            >= LIVE_COHOST_MAX_ACTIVE_SLOTS
        ):
            frappe.throw(
                "This live stream has reached its "
                "accepted or active co-host limit."
            )

    def _validate_response_fields(self):
        if self.status not in {
            ACCEPTED_STATUS,
            REJECTED_STATUS,
        }:
            return

        expected_responder = (
            self._expected_responder()
        )

        if not self.responded_by:
            frappe.throw(
                "Responded by is required."
            )

        if (
            self.responded_by
            != expected_responder
        ):
            frappe.throw(
                "The co-host response was recorded "
                "for an invalid user."
            )

        if not self.responded_at:
            frappe.throw(
                "Responded at is required."
            )

        if (
            self.status == ACCEPTED_STATUS
            and not self.accepted_at
        ):
            frappe.throw(
                "Accepted at is required."
            )

    def _validate_livekit_identity(self):
        """
        Ensure the stored participant identity is canonical.

        The co-host token must preserve the candidate's existing viewer
        identity so a role upgrade does not create a second LiveKit
        participant.
        """
        expected_identity = (
            f"user:{self.user}:"
            f"session:{self.session_id}"
        )

        if (
            self.livekit_identity
            != expected_identity
        ):
            frappe.throw(
                "Invalid LiveKit identity for "
                "the co-host session."
            )

    def _validate_active_state(self):
        if self.status != ACTIVE_STATUS:
            if self.is_active:
                frappe.throw(
                    "Only an active co-host record may "
                    "have Is Active enabled."
                )

            return

        if not self.accepted_at:
            frappe.throw(
                "The co-host request must be accepted "
                "before it becomes active."
            )

        if not self.started_at:
            frappe.throw(
                "Started at is required for "
                "an active co-host."
            )

        if not self.livekit_identity:
            frappe.throw(
                "LiveKit identity is required for "
                "an active co-host."
            )

    def _validate_end_state(self):
        if self.status != ENDED_STATUS:
            if self.ended_at:
                frappe.throw(
                    "Ended at may only be set when "
                    "the co-host status is ended."
                )

            if self.end_reason:
                frappe.throw(
                    "End reason may only be set when "
                    "the co-host status is ended."
                )

            if self.ended_by:
                frappe.throw(
                    "Ended by may only be set when "
                    "the co-host status is ended."
                )

            return

        if not self.ended_at:
            frappe.throw(
                "Ended at is required."
            )

        if not self.end_reason:
            frappe.throw(
                "End reason is required."
            )

        if (
            self.end_reason
            not in VALID_END_REASONS
        ):
            frappe.throw(
                f"Invalid co-host end reason: "
                f"{self.end_reason}."
            )

        if self.end_reason in {
            "left",
            "removed_by_host",
            "live_ended",
        } and not self.ended_by:
            frappe.throw(
                "Ended by is required for "
                "this end reason."
            )

    def _validate_timestamps(self):
        requested_at = get_datetime(
            self.requested_at
        )

        if self.expires_at:
            expires_at = get_datetime(
                self.expires_at
            )

            if expires_at <= requested_at:
                frappe.throw(
                    "Expires at must be after "
                    "requested at."
                )

        if self.responded_at:
            responded_at = get_datetime(
                self.responded_at
            )

            if responded_at < requested_at:
                frappe.throw(
                    "Responded at cannot be before "
                    "requested at."
                )

        if self.accepted_at:
            accepted_at = get_datetime(
                self.accepted_at
            )

            if accepted_at < requested_at:
                frappe.throw(
                    "Accepted at cannot be before "
                    "requested at."
                )

            if (
                self.responded_at
                and accepted_at
                < get_datetime(
                    self.responded_at
                )
            ):
                frappe.throw(
                    "Accepted at cannot be before "
                    "responded at."
                )

        if self.started_at:
            started_at = get_datetime(
                self.started_at
            )

            if not self.accepted_at:
                frappe.throw(
                    "Accepted at is required before "
                    "started at."
                )

            if (
                started_at
                < get_datetime(
                    self.accepted_at
                )
            ):
                frappe.throw(
                    "Started at cannot be before "
                    "accepted at."
                )

        if self.ended_at:
            ended_at = get_datetime(
                self.ended_at
            )

            if not self.started_at:
                frappe.throw(
                    "Started at is required before "
                    "ended at."
                )

            if (
                ended_at
                < get_datetime(
                    self.started_at
                )
            ):
                frappe.throw(
                    "Ended at cannot be before "
                    "started at."
                )

    def _validate_metadata(self):
        if self.metadata_json in (
            None,
            "",
            {},
        ):
            self.metadata_json = None
            return

        try:
            metadata = frappe.parse_json(
                self.metadata_json
            )
        except Exception:
            frappe.throw(
                "Metadata JSON must contain valid JSON."
            )

        if not isinstance(
            metadata,
            dict,
        ):
            frappe.throw(
                "Metadata JSON must contain "
                "a JSON object."
            )

        self.metadata_json = frappe.as_json(
            metadata
        )

    # HELPERS
    def _get_live(self):
        if not self.live_stream:
            return None

        return frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            self.live_stream,
            [
                "name",
                "host_user",
                "status",
                "is_active",
            ],
            as_dict=True,
        )

    def _get_previous_status(
        self,
    ) -> str | None:
        if self.is_new():
            return None

        previous = (
            self.get_doc_before_save()
        )

        if previous:
            return previous.status

        return self.get_db_value(
            "status"
        )

    def _expected_responder(
        self,
    ) -> str | None:
        live = self._get_live()

        if not live:
            return None

        if self.request_type == HOST_INVITE:
            return self.user

        if self.request_type == VIEWER_REQUEST:
            return live.host_user

        return None

    def _set_default_ended_by(self):
        if self.ended_by:
            return

        live = self._get_live()

        if self.end_reason == "left":
            self.ended_by = self.user

        elif self.end_reason in {
            "removed_by_host",
            "live_ended",
        }:
            self.ended_by = (
                live.host_user
                if live
                else None
            )
