# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: an elder in India, roughly 65 to 85, using a phone at home, often with weaker eyesight and sometimes more at home in Hindi or another Indian language than in English. They mark doses, say how they feel, and decide who may see or change their information.

Secondary: caretakers (paid or family) who mark doses and manage medicines, and family members, often in another city, who check in, read the weekly update and receive reminder emails.

## Product Purpose

CareCircle gives one elder and the people who look after them a shared, passcode-protected care circle: medicine schedules, adherence, supply and refills, doctor appointments, and escalating email reminders. Success means doses are not missed, the family knows early when something is wrong, and the elder stays in charge of their own care.

## Positioning

The elder is the owner of the circle, not its subject: only the elder adds or removes people and sets view or edit access, a bad day is shared only if the elder chooses, the emergency card is the elder's to turn on, and voice notes in the elder's own language are only suggestions until the elder confirms them.

## Operating Context

Used on phones and laptops; the elder at home, the family remotely. Reminders arrive by email (elder 15 min before a dose, caretakers at 30 min late, family at 60 min). A printed emergency card lives on the fridge; a printed visit sheet goes to the doctor. Hackathon judges try it through a one-click demo circle (Kamala Iyer, her caretaker Ravi, her daughter Priya).

## Capabilities and Constraints

- Server-rendered FastAPI + Jinja2 pages that work without JavaScript; JavaScript only enhances.
- Strict Content-Security-Policy: no external fonts, scripts or images; inline styles and scripts are not allowed.
- Must stay accessible: large text option, high contrast, keyboard use, screen readers, phone layouts, print layouts for the visit sheet and fridge card.
- Gemini features: prescription photo reading, voice notes in any Indian language, a weekly family update in 8 languages.
- No medical advice; the footer says CareCircle supports but never replaces a doctor or pharmacist.

## Brand Commitments

The name CareCircle is kept. Everything else visual (mark, colours, type) may be replaced.

## Evidence on Hand

Live app on Cloud Run with a demo circle; README screenshots in `docs/`. No logos, photos or testimonials.
