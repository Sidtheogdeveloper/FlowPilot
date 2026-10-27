# FlowPilot account security

Phase 3 introduces public email/password registration, Argon2id password hashing,
opaque database-backed sessions, HttpOnly SameSite cookies, CSRF checks, origin
checks, persistent login throttling and account ownership for datasets, workflow
versions, pending clarifications and artifact downloads. Sessions expire after one
hour of inactivity or 24 hours absolutely; users can revoke sessions.

The database and artifact store remain trusted internal services. Workers use the
workflow owner's identity when creating artifacts. Identical content can be shared
physically while download grants remain per account. No raw password or session
token is stored in the database. Browser tokens are not placed in localStorage.

Existing anonymous datasets and workflows stay unassigned and invisible to public
accounts. Do not assign them automatically to the first registrant.

Use HTTPS and APP_ENV=production before exposing the application publicly. Local
HTTP development uses non-Secure cookies intentionally. Do not expose PostgreSQL
or worker interfaces. Email verification/recovery requires an SMTP service.

References: [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html),
[session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

This implementation has automated security regression tests; it has not undergone
an independent penetration test.
