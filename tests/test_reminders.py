from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.core.reminders import ReminderStore, reminder_message
from backend.memory.database import Database


def test_one_time_reminder_is_claimed_once(tmp_path) -> None:
    database = Database(tmp_path / "reminders.db")
    database.initialize()
    store = ReminderStore(database)
    now = datetime.now(UTC)
    created = store.create(
        "aula de inglês",
        (now + timedelta(minutes=20)).isoformat(),
        minutes_before=10,
    )

    assert store.claim_due(now + timedelta(minutes=9)) == []
    claimed = store.claim_due(now + timedelta(minutes=11))
    assert [item["id"] for item in claimed] == [created["id"]]
    assert "daqui a 9 minutos" in reminder_message(
        claimed[0], now + timedelta(minutes=11)
    )
    assert store.claim_due(now + timedelta(minutes=12)) == []


def test_weekly_reminder_is_rescheduled(tmp_path) -> None:
    database = Database(tmp_path / "weekly.db")
    database.initialize()
    store = ReminderStore(database)
    now = datetime.now(UTC)
    created = store.create(
        "aula de inglês",
        (now + timedelta(minutes=5)).isoformat(),
        recurrence="weekly",
    )
    assert store.claim_due(now + timedelta(minutes=6))[0]["id"] == created["id"]
    active = store.list()[0]
    assert datetime.fromisoformat(active["due_at"]) > now + timedelta(days=6)


def test_reminder_update_recalculates_alert_time(tmp_path) -> None:
    database = Database(tmp_path / "update.db")
    database.initialize()
    store = ReminderStore(database)
    now = datetime.now(UTC)
    created = store.create(
        "Aula de inglês",
        (now + timedelta(days=2)).replace(hour=21, minute=50).isoformat(),
        recurrence="weekly",
        minutes_before=50,
    )
    new_due = (now + timedelta(days=2)).replace(hour=21, minute=0).isoformat()
    updated = store.update(created["id"], due_at=new_due, minutes_before=10)
    due = datetime.fromisoformat(updated["due_at"])
    alert = datetime.fromisoformat(updated["alert_at"])
    assert due - alert == timedelta(minutes=10)
    assert due.minute == 0


def test_medication_reminder_has_clear_spoken_instruction() -> None:
    message = reminder_message({"title": "tomar meu remédio", "minutes_before": 0})
    assert "tomar seu remédio" in message
    assert "beber água" in message


def test_reminder_message_uses_real_remaining_time() -> None:
    now = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
    reminder = {
        "title": "aula de inglês",
        "due_at": (now + timedelta(minutes=7)).isoformat(),
        "minutes_before": 30,
    }
    assert "daqui a 7 minutos" in reminder_message(reminder, now)


def test_late_reminder_does_not_claim_future_time() -> None:
    now = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
    reminder = {
        "title": "consulta",
        "due_at": (now - timedelta(minutes=4)).isoformat(),
        "minutes_before": 15,
    }
    message = reminder_message(reminder, now)
    assert "atrasado" in message
    assert "há 4 minutos" in message
    assert "daqui" not in message
