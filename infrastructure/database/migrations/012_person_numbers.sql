-- A stable registry number is a person's label; it is not a camera track ID.
ALTER TABLE fr_persons ADD COLUMN IF NOT EXISTS person_number bigint;

WITH ordered_people AS (
    SELECT id,
           COALESCE((SELECT MAX(person_number) FROM fr_persons), 0)
             + row_number() OVER (ORDER BY created_at ASC, id ASC) AS assigned_number
    FROM fr_persons
    WHERE person_number IS NULL
)
UPDATE fr_persons AS person
SET person_number = ordered_people.assigned_number
FROM ordered_people
WHERE person.id = ordered_people.id;

CREATE UNIQUE INDEX IF NOT EXISTS fr_persons_person_number_unique
    ON fr_persons(person_number);
ALTER TABLE fr_persons ALTER COLUMN person_number SET NOT NULL;

CREATE TABLE IF NOT EXISTS fr_person_number_counter (
    counter_id smallint PRIMARY KEY CHECK (counter_id = 1),
    next_number bigint NOT NULL CHECK (next_number > 0)
);
INSERT INTO fr_person_number_counter(counter_id, next_number)
SELECT 1, COALESCE(MAX(person_number), 0) + 1 FROM fr_persons
ON CONFLICT (counter_id) DO UPDATE
SET next_number = GREATEST(fr_person_number_counter.next_number, EXCLUDED.next_number);

-- Older index writers still insert people without the registry number column.
-- Assign them a number atomically while keeping the number immutable on updates.
CREATE OR REPLACE FUNCTION fr_assign_person_number()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.person_number IS NULL THEN
        UPDATE fr_person_number_counter
        SET next_number = next_number + 1
        WHERE counter_id = 1
        RETURNING next_number - 1 INTO NEW.person_number;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS fr_person_number_on_insert ON fr_persons;
CREATE TRIGGER fr_person_number_on_insert
BEFORE INSERT ON fr_persons
FOR EACH ROW EXECUTE FUNCTION fr_assign_person_number();
