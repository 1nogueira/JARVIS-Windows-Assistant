from __future__ import annotations

from backend.security.permissions import PermissionLevel, PermissionPolicy


def test_safe_tool_does_not_require_confirmation(settings_store):
    policy = PermissionPolicy(settings_store)
    assert not policy.requires_confirmation("metrics", PermissionLevel.SAFE)


def test_confirm_tool_requires_confirmation(settings_store):
    policy = PermissionPolicy(settings_store)
    assert policy.requires_confirmation("write", PermissionLevel.CONFIRM)


def test_restricted_cannot_be_downgraded_to_safe(settings_store):
    settings_store.update({"permissions": {"shutdown": "SAFE"}})
    policy = PermissionPolicy(settings_store)
    assert policy.level_for("shutdown", PermissionLevel.RESTRICTED) is PermissionLevel.CONFIRM


def test_user_can_raise_safe_permission(settings_store):
    settings_store.update({"permissions": {"metrics": "CONFIRM"}})
    policy = PermissionPolicy(settings_store)
    assert policy.requires_confirmation("metrics", PermissionLevel.SAFE)

