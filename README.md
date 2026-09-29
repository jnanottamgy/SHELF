# SHELF

**S**coped **H**osted **E**xplorer for **L**inked **F**iles.

A student's own drive, in a browser. It sits over the same Postgres and object
store as [Academic Assistant](https://github.com/Kartikkatwe/Academic-Assistant)
— the WhatsApp study assistant students forward their files to — and answers
the retrieval questions that would otherwise be chat messages.

That is the whole business case. From 1 October 2026 Meta bills service
conversations per message, so a student who opens a folder instead of asking
"send me my DBMS notes" costs nothing. The product case is simpler: *"all my
notes"* is a bad question to answer in a chat window and an easy one to answer
on a page.

```bash
uv sync
cp .env.example .env          # point DATABASE_URL at the assistant's database
uv run uvicorn shelf.main:app --reload
uv run pytest                 # 44 tests
```

---

## What it is not

**It does not own the schema.** The assistant's repository holds the Alembic
chain and is the only place a migration is ever run. Two services racing
`alembic upgrade head` against one database is how a half-applied schema
happens. `shelf/models/` is a **read-only mirror**, `schema/` is a committed
dump of what the real chain produces, and `tests/test_schema_drift.py` fails
when a column this service reads has moved. See [Schema](#schema) below —
that drift is the one real cost of splitting this repo out, and it is worth
understanding before you touch the models.

**It cannot write anything a student can see.** No filing, renaming, deleting
or uploading. The product's promise is that talking to the assistant is
enough, and a second way to change the same rows would be a second set of
rules about what is allowed — with the gates, the tickets and the one-reply
invariant all living on the other side. SHELF shows; the chat decides.

**It cannot mint a credential.** Login links are created by the assistant, in
reply to a student asking for one. This service only ever *spends* them. That
split is deliberate: the process exposed to the open internet is not the one
that can create access. There is a test asserting `issue_link` does not exist
here, so nobody adds it back for convenience.

**It writes exactly one thing.** A `signup_codes` row — a *claim* on a phone
number, which is evidence of nothing. The proof is a WhatsApp message arriving
from that number carrying the code, and only the assistant can observe one.
`users.paid_until` is still the credential and is still written only by the
payment webhook, so writing claims here does not make this service able to
hand out access. See [Signing up](#signing-up).

**It holds no Meta token, no LLM key and no payment secret.** Not because they
are guarded, but because they are absent — this service sends no messages and
takes no money. A process that cannot reach Meta cannot leak a Meta token.

---

## Signing up

A number is proved reachable **before** money is taken against it. The
assistant answers an unknown number with silence, so a payment captured
against a mistyped number is invisible from every direction — no bounce, no
error, nothing until the student gives up. Verifying first makes that state
unreachable rather than merely alertable.

1. The student enters their number here. We normalise it and write a
   `signup_codes` row: a six-character code, bound to that number, good for
   ten minutes.
2. They send that code to the assistant **from their own WhatsApp**. The
   message arriving from that number is the proof.
3. The assistant marks the row verified and confirms in the chat. This page
   is polling and moves them on.

**The direction is the security property.** A code travelling *to* a phone can
be read off a lock screen or talked out of someone on a call; a code
travelling *from* one cannot, because possession of the phone is the act
itself. It also costs nothing — the student speaks first, so the confirmation
is inside WhatsApp's service window and needs no approved template — and it
needs no SMS vendor.

Polling is keyed by the **code**, never by the number. Someone holding a code
was given it by this page, so asking about it tells them nothing new; keying
it by number would make it a way to ask whether a stranger is signing up.

`/checkout` is the gate: nothing unverified gets past it toward a payment
provider. Everything the webhook later sees is therefore verified by
construction, and the webhook stays permissive — a payment taken and not
recorded is the one state worse than an unverified signup.

The code alphabet and TTL are **this repository's** choice. The assistant does
not validate against them, deliberately: a validator there that disagreed with
the generator here would mean no code ever verifies, silently, with neither
repository's tests noticing. The database lookup is the validation.

---

## Signing in

There is no password, no email and no login form, and that is finished rather
than unbuilt. A `users` row is a phone number and a paid-until date, so the
only thing anyone can demonstrate is possession of the phone.

1. The student says **"my files"** to the assistant, in the chat that already
   proves it. Deterministic shortcut — no LLM call.
2. The assistant replies with a one-time URL, `SITE_URL/f/<token>`. They spoke
   first, so this is inside WhatsApp's 24-hour service window: no template
   approval, no business-initiated charge.
3. Opening it **rotates** the link — the row the token named is deleted, a
   fresh secret is written in its place and moved into an HttpOnly cookie, and
   the browser is redirected so the token leaves the address bar.

Rotation is what makes the link single-use, which is why there is no table
recording spent tokens: the id that travelled through the chat stops existing.
A chat message is forever, so the link inside it must not be.

Nothing here can be phished the way an emailed or SMS code can. There is no
code for a student to read out and no field anywhere asking for their number,
so *"send me the code you just got"* has nothing to ask for.

### Why rows and not a signed token

Because access has to be **withdrawable**. A JWT is valid until it expires; a
row is valid until it is deleted. Three things must take effect on the *next*
request, not eventually:

- a subscription that lapses — re-checked every request, grace included, so
  the site and the chat agree about who is being served;
- a device evicted by the cap;
- a DPDP erasure, which removes the session rows with everything else.

Two signed-in browsers per student. A third **evicts the least recently seen**
rather than refusing the new one: the person who hits that cap has usually
just replaced a phone, and "sign out on the old device" is not an instruction
they can follow.

---

## The tree

```
/files                        terms, newest first, plus Miscellaneous
  /files/t/<term>             the subjects in that term
  /files/t/<term>/admin       every Info & Admin shelf in it, gathered
  /files/s/<subject>          three shelves, always all three
  /files/s/<subject>/<shelf>  the files on one shelf
/files/misc                   everything with no subject
/files/search?q=              the same matcher the assistant uses
/files/deadlines              what is still due
/files/r/<resource>           one file, or a note rendered as a page
/f/<token>                    spend a login link
```

**A folder is a `GROUP BY`, not a row.** No folders table, and there should not
be one: a derived tree cannot disagree with what is actually filed, while a
real one would need creating, renaming, moving and repairing that the student
never asked for.

**Three fixed shelves** (`shelf/categories.py`): Material, PYQ & Questions,
Info & Admin. The shelf is inferred from what the student said — "dbms previous
year papers" → `pyq` — but resolves to one of three rather than being stored
verbatim. Free-form types grow a folder per phrasing ("notes", "note", "lecture
notes"), and forty folders in a subject is worse than no drive at all.

All three render in every subject even when empty. A tree whose shape depends
on what is already in it cannot be learned. `category IS NULL` is **not** a
fourth shelf — it means nobody named one, and it reads as the default.

The per-term **admin drawer** is a cross-section, not a folder. Nothing belongs
to a term alone — a resource hangs off a subject and a subject off a term — so
rather than add `resources.term_id` to make a folder render, it gathers that
term's Info & Admin shelves. Nothing moves, and a timetable filed under DBMS is
still on DBMS's shelf.

### Files are proxied, not presigned

`/files/r/<id>` reads the object and returns the bytes. A signed R2 URL would be
cheaper in bandwidth and is the wrong trade: it is issued once and cannot be
withdrawn, so it would keep working after a lapse, after a device eviction, and
after an erasure had deleted every row that mentioned it. R2's egress to us is
free, so proxying costs this service's own bandwidth and buys the same check on
every byte handed over.

---

## Schema

`shelf/models/` mirrors seven tables — `users`, `terms`, `subjects`,
`resources`, `deadlines`, `web_sessions`, `signup_codes` — and owns none of
them. It writes only to `signup_codes`, and only claims.

Copies rot, and this one would rot *silently*: a migration lands in the
assistant, nobody thinks it concerns SHELF, and the first symptom is a 500 on
whichever page reads the column that moved, seen only by the students who open
it. Three things guard against that:

| | |
|---|---|
| `schema/academic-assistant.sql` | A committed dump of what the assistant's migration chain produces. Tests build the test database from **this**, never from `create_all()` on the models — otherwise the mirror would be tested against itself. |
| `tests/test_schema_drift.py` | Reflects the live database and fails when a column the mirror declares is missing, or has become nullable underneath it. |
| The diff | Refreshing the snapshot shows up in a pull request. That is what catches a column that was **added** and ought to be mirrored — the drift test cannot, because nothing here knows it was meant to exist. |

Refresh the snapshot with:

```bash
pg_dump --schema-only --no-owner --no-privileges <assistant db> \
  | python scripts/trim_dump.py > schema/academic-assistant.sql
```

Both services write `web_sessions`, with disjoint operations — the assistant
inserts unopened links, SHELF activates, touches and deletes. Same shape of
coupling as the payment site writing `users`, and for the same reason: the
table is the contract.

---

## Configuration

| Setting | What it does |
|---|---|
| `DATABASE_URL` | The assistant's database. A bare `postgresql://` from a managed host is rewritten to use psycopg, so paste it unedited. |
| `GRACE_DAYS` | **Must match the assistant's.** If it does not, a student in grace is answered in chat and locked out of their own files, which reads as broken. |
| `WEB_LINK_MINUTES` | How long an unopened link stays good. Default 15. |
| `WEB_SESSION_DAYS` | Idle timeout for a signed-in browser. Default 30. |
| `WEB_DEVICES` | Signed-in browsers per student. Default 2. |
| `WHATSAPP_CHAT_URL` | Where the dead-end pages send someone whose link expired. |
| `R2_*` | Object storage. Empty falls back to an in-memory store — fine on a laptop, and silently destructive in production. |

The session cookie is `Secure` only when `APP_ENV=prod`; over plain HTTP on a
laptop a secure cookie is never set and nothing works.

---

## Known gaps

- **No "show more" on a long shelf.** Every file renders. Fine at a term's
  worth, not at a degree's.
- **Search is the assistant's matcher**, so it has the assistant's limits:
  every word must appear in the title, body or filename.
- **A file with no subject has no term**, so it lands in Miscellaneous at the
  root rather than under the semester it arrived in. There is nothing in the
  schema tying it to one.
