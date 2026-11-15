"""Domain models for CareCircle, validated with Pydantic."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, EmailStr, Field, field_validator

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Warn the circle when a medicine has this many days (or fewer) left.
REFILL_DAYS = 5

BLOOD_GROUPS = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")


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


class Feeling(StrEnum):
    GOOD = "good"
    OKAY = "okay"
    UNWELL = "unwell"

    @property
    def label(self) -> str:
        return {"good": "Good", "okay": "Okay", "unwell": "Not well"}[self.value]


class Circle(BaseModel):
    """One elder's shared care profile."""

    id: str
    elder_name: str = Field(min_length=1, max_length=80)
    timezone: str = "Asia/Kolkata"
    created_at: datetime
    demo: bool = False
    # Shown on the emergency card. The elder decides whether the card is on.
    blood_group: str = Field(default="", max_length=3)
    allergies: str = Field(default="", max_length=200)
    conditions: str = Field(default="", max_length=200)
    emergency_contacts: str = Field(default="", max_length=300)
    # Random id in the public emergency-card link; None while the card is off.
    card_id: str | None = None

    @field_validator("blood_group")
    @classmethod
    def _known_blood_group(cls, value: str) -> str:
        if value and value not in BLOOD_GROUPS:
            raise ValueError("unknown blood group")
        return value


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
    # Doses in hand, counted down when a dose is marked taken. None = not tracked.
    supply: int | None = Field(default=None, ge=0, le=9999)
    refill_alert_sent: bool = False

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

    @property
    def days_left(self) -> int | None:
        """Whole days of medicine left at the current dose times, if supply is tracked."""
        if self.supply is None:
            return None
        return self.supply // len(self.times)

    @property
    def running_low(self) -> bool:
        return self.active and self.days_left is not None and self.days_left <= REFILL_DAYS


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


class CheckIn(BaseModel):
    """How the elder said they felt on one local day (one per day, latest wins)."""

    id: str
    circle_id: str
    day: date
    feeling: Feeling
    note: str = Field(default="", max_length=280)
    recorded_by: str
    recorded_at: datetime


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
