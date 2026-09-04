-- Smart Inbox — first-run bootstrap for the FREEPDB1 pluggable database.
--
-- Runs once, from gvenzl/oracle-free's /container-entrypoint-initdb.d hook, as
-- `sqlplus / as sysdba` against the CDB root — hence the explicit ALTER SESSION below.
-- The application user itself has already been created by the image at this point, from the
-- APP_USER / APP_USER_PASSWORD environment variables set in docker-compose.yml.
--
-- Schema objects are NEVER created here. Every table, index, package, view and trigger comes
-- from a Flyway migration under backend/src/main/resources/db/migration (CLAUDE.md
-- conventions), so the schema can be rebuilt from zero and its history read in git.

ALTER SESSION SET CONTAINER = FREEPDB1;

SET SERVEROUTPUT ON

-- Grant the privileges the application needs beyond the image's defaults, to whichever user
-- the image created. Looking the user up (rather than hard-coding SMARTINBOX) keeps this
-- correct if ORACLE_APP_USER is changed in .env.
DECLARE
  v_granted PLS_INTEGER := 0;
BEGIN
  FOR u IN (
    SELECT username
      FROM dba_users
     WHERE oracle_maintained = 'N'
       AND username NOT IN ('PDBADMIN', 'FLYWAY')
  ) LOOP
    EXECUTE IMMEDIATE
      'GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE, '
      || 'CREATE PROCEDURE, CREATE TRIGGER, CREATE TYPE, CREATE SYNONYM TO ' || u.username;
    EXECUTE IMMEDIATE 'ALTER USER ' || u.username || ' QUOTA UNLIMITED ON USERS';
    DBMS_OUTPUT.PUT_LINE('Smart Inbox: privileges granted to ' || u.username);
    v_granted := v_granted + 1;
  END LOOP;

  IF v_granted = 0 THEN
    DBMS_OUTPUT.PUT_LINE('Smart Inbox: WARNING - no application user found to grant to.');
  END IF;
END;
/

EXIT;
