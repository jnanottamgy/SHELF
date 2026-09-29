-- Schema snapshot of the Academic Assistant database.
--
-- NOT the source of truth and NOT edited by hand. The assistant's repository
-- owns the Alembic chain and is the only place a migration is ever run; this
-- is a dump of what that chain produces, committed here so SHELF's tests have
-- a real schema to run against without cross-repo credentials -- and so that a
-- schema change shows up in a SHELF pull request as a diff somebody reads.
--
-- Refresh it with:
--   pg_dump --schema-only --no-owner --no-privileges <assistant db> \
--     | python scripts/trim_dump.py > schema/academic-assistant.sql
--
-- If this file changes, check shelf/models/ against it. tests/test_schema_drift.py
-- fails when a column SHELF reads is missing from the live database, but it
-- cannot tell you about a column that was added and ought to be mirrored.


CREATE TYPE public.message_direction AS ENUM (
    'inbound',
    'outbound'
);

CREATE TYPE public.recurrence_rule AS ENUM (
    'daily',
    'weekly',
    'monthly'
);

CREATE TYPE public.reminder_status AS ENUM (
    'pending',
    'fired',
    'cancelled',
    'failed'
);

CREATE TYPE public.resource_status AS ENUM (
    'pending_clarification',
    'ready'
);

CREATE TYPE public.term_type AS ENUM (
    'semester',
    'trimester',
    'quarter',
    'annual'
);

CREATE TYPE public.ticket_status AS ENUM (
    'open',
    'closed'
);

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);

CREATE TABLE public.deadline_resources (
    deadline_id bigint NOT NULL,
    resource_id bigint NOT NULL
);

CREATE TABLE public.deadlines (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    due_at timestamp with time zone NOT NULL,
    title text NOT NULL,
    description text,
    subject_id bigint,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE public.deadlines ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.deadlines_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.messages (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    wa_message_id text NOT NULL,
    direction public.message_direction NOT NULL,
    type text NOT NULL,
    body text,
    context_id text,
    ticket_id bigint,
    resource_id bigint,
    created_at timestamp with time zone NOT NULL,
    purpose text,
    action_id text,
    covers_through_id bigint
);

ALTER TABLE public.messages ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.reminders (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    fire_at timestamp with time zone NOT NULL,
    body text NOT NULL,
    status public.reminder_status NOT NULL,
    recurrence public.recurrence_rule,
    deadline_id bigint,
    created_at timestamp with time zone NOT NULL,
    template text
);

ALTER TABLE public.reminders ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.reminders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.resources (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    subject_id bigint,
    status public.resource_status NOT NULL,
    content_type text,
    mime_type text,
    title text,
    storage_ref text,
    created_at timestamp with time zone NOT NULL,
    body text,
    filename text,
    category text
);

ALTER TABLE public.resources ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.resources_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.signup_codes (
    code text NOT NULL,
    ph_no text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    verified_at timestamp with time zone,
    CONSTRAINT ck_signup_codes_ph_no_digits CHECK ((ph_no ~ '^91[0-9]{10}$'::text))
);

CREATE TABLE public.subject_aliases (
    id bigint NOT NULL,
    subject_id bigint NOT NULL,
    alias text NOT NULL,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE public.subject_aliases ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.subject_aliases_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.subjects (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    term_id bigint NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE public.subjects ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.subjects_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.terms (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    sequence_no smallint NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE public.terms ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.terms_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.tickets (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    problem_statement text NOT NULL,
    status public.ticket_status NOT NULL,
    attachment_ref bigint,
    ambiguous_solution text,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE public.tickets ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.tickets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.users (
    id bigint NOT NULL,
    ph_no character varying(20) NOT NULL,
    name text,
    term_system public.term_type,
    created_at timestamp with time zone NOT NULL,
    paid_until timestamp with time zone NOT NULL,
    subscription_id text,
    CONSTRAINT ck_users_ph_no_digits CHECK (((ph_no)::text ~ '^91[0-9]{10}$'::text))
);

ALTER TABLE public.users ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.web_sessions (
    id text NOT NULL,
    user_id bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    activated_at timestamp with time zone
);

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);

ALTER TABLE ONLY public.deadline_resources
    ADD CONSTRAINT deadline_resources_pkey PRIMARY KEY (deadline_id, resource_id);

ALTER TABLE ONLY public.deadlines
    ADD CONSTRAINT deadlines_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_wa_message_id_key UNIQUE (wa_message_id);

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.resources
    ADD CONSTRAINT resources_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.signup_codes
    ADD CONSTRAINT signup_codes_pkey PRIMARY KEY (code);

ALTER TABLE ONLY public.subject_aliases
    ADD CONSTRAINT subject_aliases_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.terms
    ADD CONSTRAINT terms_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tickets
    ADD CONSTRAINT tickets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.subject_aliases
    ADD CONSTRAINT uq_subject_aliases_subject_id_alias UNIQUE (subject_id, alias);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_ph_no_key UNIQUE (ph_no);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.web_sessions
    ADD CONSTRAINT web_sessions_pkey PRIMARY KEY (id);

CREATE INDEX ix_messages_user_id_id ON public.messages USING btree (user_id, id);

CREATE INDEX ix_reminders_status_fire_at ON public.reminders USING btree (status, fire_at);

CREATE INDEX ix_signup_codes_ph_no ON public.signup_codes USING btree (ph_no);

CREATE INDEX ix_subject_aliases_alias ON public.subject_aliases USING btree (alias);

CREATE INDEX ix_tickets_user_id_status ON public.tickets USING btree (user_id, status);

CREATE INDEX ix_web_sessions_user_id ON public.web_sessions USING btree (user_id);

ALTER TABLE ONLY public.deadline_resources
    ADD CONSTRAINT deadline_resources_deadline_id_fkey FOREIGN KEY (deadline_id) REFERENCES public.deadlines(id);

ALTER TABLE ONLY public.deadline_resources
    ADD CONSTRAINT deadline_resources_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id);

ALTER TABLE ONLY public.deadlines
    ADD CONSTRAINT deadlines_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);

ALTER TABLE ONLY public.deadlines
    ADD CONSTRAINT deadlines_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_ticket_id_fkey FOREIGN KEY (ticket_id) REFERENCES public.tickets(id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_deadline_id_fkey FOREIGN KEY (deadline_id) REFERENCES public.deadlines(id);

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.resources
    ADD CONSTRAINT resources_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);

ALTER TABLE ONLY public.resources
    ADD CONSTRAINT resources_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.subject_aliases
    ADD CONSTRAINT subject_aliases_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_term_id_fkey FOREIGN KEY (term_id) REFERENCES public.terms(id);

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.terms
    ADD CONSTRAINT terms_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.tickets
    ADD CONSTRAINT tickets_attachment_ref_fkey FOREIGN KEY (attachment_ref) REFERENCES public.resources(id);

ALTER TABLE ONLY public.tickets
    ADD CONSTRAINT tickets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.web_sessions
    ADD CONSTRAINT web_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);
