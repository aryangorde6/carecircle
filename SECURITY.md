# Security policy

CareCircle holds health-related information about older people, so security and privacy are part of the design, not an add-on.

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's **Report a vulnerability** button on this repository's Security tab (private vulnerability reporting). Include the steps to reproduce and the impact you expect. You will get a reply within a few days.

## What is protected, and how

| Risk | Protection |
|---|---|
| Guessing passcodes | Argon2id hashes, a unique passcode per person, strength checks, rate limiting per client and per circle |
| Seeing another family's data | Every read and write is scoped to the signed-in member's own circle path; permissions are re-read on every request |
| Cross-site request forgery | A CSRF token on every state-changing form, plus SameSite cookies |
| Cross-site scripting | Autoescaped templates, a strict Content-Security-Policy with no inline scripts or styles |
| Malicious uploads | Images only, checked by magic bytes, capped at 5 MB, processed in memory and never stored |
| Prompt injection | User text and images are passed to Gemini as data; output is schema-validated and reviewed by a person; the model has no tools |
| Runaway AI cost | A hard daily cap on Gemini calls and a per-member hourly limit |
| Forged scheduler calls | The reminder endpoint accepts only Google-signed OIDC tokens for one audience and service account |
| Leaked secrets | No keys in code: Google services use the Cloud Run service account; other secrets come from Secret Manager |
| Unwanted sharing | A "Not well" check-in emails the circle only if the elder chooses |
| Vulnerable dependencies | Pinned versions, `pip-audit` on every push |

More detail is in the [Security section of the README](README.md#security).
