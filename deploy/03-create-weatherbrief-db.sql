-- Run once on shared MySQL to set up the weatherbrief database.
--
-- SECURITY: You MUST replace 'CHANGE_ME' with a strong password before running.
-- Generate one with: openssl rand -base64 24
--
-- The user is restricted to the Docker shared-services network.
-- Adjust the subnet if your Docker network uses a different range.

CREATE DATABASE IF NOT EXISTS weatherbrief
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'weatherbrief'@'172.%' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT, INSERT, UPDATE, DELETE ON weatherbrief.* TO 'weatherbrief'@'172.%';
FLUSH PRIVILEGES;
