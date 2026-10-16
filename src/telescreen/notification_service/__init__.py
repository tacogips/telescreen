"""Standalone Notification service process."""

from telescreen.notification_service.app import NotificationRuntime, build_notification_runtime

__all__: list[str] = ["NotificationRuntime", "build_notification_runtime"]
