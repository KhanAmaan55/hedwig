-- 0007 — Which mood produced which reply (docs/05 §5.1, docs/07 §14.2).
--
-- docs/05 §5.1 has always specified `message.emotion_ref REFERENCES emotion_history(id)` —
-- "state when produced" — and nothing has ever written it, because until the emotion engine
-- existed there was no state to reference.
--
-- It is what makes `/v1/explain` able to answer "why did you put it like that?" rather than
-- only "why did you bring that up?". A mood timeline beside a conversation is decoration;
-- a mood timeline joined to the messages it produced is an explanation.

ALTER TABLE message ADD COLUMN emotion_ref TEXT REFERENCES emotion_history(id);

CREATE INDEX idx_message_emotion ON message (emotion_ref) WHERE emotion_ref IS NOT NULL;
