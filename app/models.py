"""Domain models for CareCircle, validated with Pydantic."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, EmailStr, Field, field_validator

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Role(StrEnum):
    ELDER = "elder"
    CARETAKER = "caretaker"
    FAMILY = "family"


class Access(StrEnum):
    VIEW = "view"
    EDIT = "edit"


class DoseStatus(StrEnum):
    TAKEN = "taken"
    SKIPPED = "skipped"


class Circle(BaseModel):
    """One elder's shared care profile."""

    id: str
    elder_name: str = Field(min_length=1, max_length=80)
    timezone: str = "Asia/Kolkata"
    created_at: datetime
    demo: bool = False


class Member(BaseModel):
    """A person with passcode access to a circle."""

    id: str
    circle_id: str
    name: str = Field(min_length=1, max_length=80)
    role: Role
    access: Access
    email: EmailStr | None = None
    passcode_hash: str
    notify: bool = True

    @property
    def can_edit(self) -> bool:
        return self.role == Role.ELDER or self.access == Access.EDIT

    @property
    def is_elder(self) -> bool:
        return self.role == Role.ELDER


class Medication(BaseModel):
    """A medicine with daily dose times."""

    id: str
    circle_id: str
    name: str = Field(min_length=1, max_length=80)
    dosage: str = Field(default="", max_length=60)
    instructions: str = Field(default="", max_length=200)
    times: list[str] = Field(min_length=1, max_length=8)
    start_date: date
    end_date: date | None = None
    active: bool = True

    @field_validator("times")
    @classmethod
    def _validate_times(cls, value: list[str]) -> list[str]:
        cleaned = sorted({t.strip() for t in value if t.strip()})
        if not cleaned:
            raise ValueError("at least one dose time is required")
        for item in cleaned:
            if not TIME_PATTERN.match(item):
                raise ValueError(f"invalid time {item!r}; use HH:MM")
        return cleaned

    def is_scheduled_on(self, day: date) -> bool:
        if not self.active or day < self.start_date:
            return False
        return self.end_date is None or day <= self.end_date


class DoseLog(BaseModel):
    """What happened to one scheduled dose, plus which reminders were sent."""

    id: str
    circle_id: str
    medication_id: str
    scheduled_for: datetime
    status: DoseStatus | None = None
    recorded_by: str | None = None
    recorded_at: datetime | None = None
    reminders_sent: list[str] = Field(default_factory=list)


class Appointment(BaseModel):
    """A doctor's appointment."""

    id: str
    circle_id: str
    doctor: str = Field(min_length=1, max_length=80)
    purpose: str = Field(default="", max_length=120)
    location: str = Field(default="", max_length=120)
    starts_at: datetime
    notes: str = Field(default="", max_length=500)
    reminders_sent: list[str] = Field(default_factory=list)


class ScheduledDose(BaseModel):
    """A dose expected at a specific time, joined with its log if any."""

    medication: Medication
    scheduled_for: datetime
    log: DoseLog | None = None

    @property
    def status(self) -> DoseStatus | None:
        return self.log.status if self.log else None


class ExtractedMedication(BaseModel):
    """A medication suggested by Gemini from a prescription photo."""

    name: str = Field(min_length=1, max_length=80)
    dosage: str = Field(default="", max_length=60)
    times: list[str] = Field(default_factory=list, max_length=8)
    instructions: str = Field(default="", max_length=200)

    @field_validator("times")
    @classmethod
    def _keep_valid_times(cls, value: list[str]) -> list[str]:
        return sorted({t.strip() for t in value if TIME_PATTERN.match(t.strip())})
