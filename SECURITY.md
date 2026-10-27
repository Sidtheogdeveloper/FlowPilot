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

## Phase 4 additions

Profile editing, password changes, single-use email verification/reset links and
production verification gating are implemented. Password changes and resets revoke
other sessions. Mail tokens are hashed in PostgreSQL and expire in 30 minutes.
Mail links use a configured public origin and URL fragments to keep tokens out of
HTTP request URLs. SMTP is TLS-protected in production. The frontend ships a
restrictive Content Security Policy; downloaded reports are sandboxed.

SMTP and HTTPS hosting must be configured and independently verified before
public deployment. Local email delivery is intentionally unavailable without SMTP.

## Explicit team snapshots

Global dataset/workflow/artifact ownership filters remain unchanged. Teams use
accepted email invitations and an explicit item-sharing action. Snapshot reads
require active membership; downloads additionally require the exact artifact
reference to be in that snapshot's allowlist. A viewer receives no permanent
artifact grant. Editors may request private copies of explicitly shared inputs;
those copies belong to the editor and do not expose the owner's other resources.
Unsharing/removing membership blocks subsequent snapshot requests. Already
downloaded or privately copied data cannot be recalled.

Document content is treated as text, not instructions. Extraction is bounded by
upload, expanded archive, extracted text, page and passage limits. The executor
accepts approved document handlers only and checks retrieved citations against
accepted immutable source passages. Scanned documents are not OCR-processed.
Reuse is limited to the same owner/chat with matching executor/input fingerprints.
Exported spreadsheet text is escaped against formula interpretation.
