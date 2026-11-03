-- REVIEW ONLY: run explicitly as the PostgreSQL administrator after authorization.
-- Stop on any existing name. Do not run with --single-transaction (CREATE DATABASE).
\set ON_ERROR_STOP on
CREATE ROLE sdiego_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE sdiego_migrator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE sdiego_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT sdiego_owner TO sdiego_migrator WITH INHERIT FALSE;
CREATE DATABASE sdiego_backoffice OWNER sdiego_owner;
REVOKE ALL ON DATABASE sdiego_backoffice FROM PUBLIC;
GRANT CONNECT ON DATABASE sdiego_backoffice TO sdiego_app, sdiego_migrator;
\connect sdiego_backoffice
ALTER SCHEMA public OWNER TO sdiego_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO sdiego_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sdiego_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sdiego_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sdiego_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO sdiego_app;
-- Set both LOGIN passwords separately with interactive psql \password commands.
-- Do not put passwords in this file, shell arguments or deployment logs.
