-- Run once on shared MySQL to set up the weatherbrief database.
-- Replace 'CHANGE_ME' with a strong password before running.

CREATE DATABASE IF NOT EXISTS weatherbrief
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'weatherbrief'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT ALL PRIVILEGES ON weatherbrief.* TO 'weatherbrief'@'%';
FLUSH PRIVILEGES;
