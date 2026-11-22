-- Separate database for pytest so test runs never touch local dev data.
-- Runs once, when the compose volume is first created.
CREATE DATABASE sharp_tracker_test OWNER sharp;
