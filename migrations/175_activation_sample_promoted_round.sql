-- Retain the exact formal round associated with an Activation promotion task.
ALTER TABLE sample_contents ADD COLUMN promoted_round_id INTEGER;
