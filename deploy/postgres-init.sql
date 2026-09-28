-- The app must not connect as a superuser: superusers bypass Row-Level Security.
CREATE ROLE khatti_app LOGIN PASSWORD 'khatti_app' NOSUPERUSER NOBYPASSRLS;
ALTER SCHEMA public OWNER TO khatti_app;
