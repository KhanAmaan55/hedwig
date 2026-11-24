"""The deterministic half of corrections in conversation (docs/06 §19): the gate and the checks."""

from __future__ import annotations

import pytest

from hedwig.memory.corrections import acceptable, sounds_like_correction

CORRECTIONS = [
    "No, she lives in Lisbon.",
    "No — she lives in Lisbon now.",
    "Nope, it's July, not June.",
    "Actually, Ana lives in Lisbon now.",
    "That's not right, I'm vegetarian.",
    "That's wrong: my sister is called Ana.",
    "You've got that wrong, we fly in July.",
    "Not Porto, Lisbon.",
    "Not London — Manchester.",
    "Lisbon, not Porto.",
    "I don't drink coffee anymore.",
    "I no longer work at Acme.",
    "Correction: the trip is in July.",
    "I'm not vegan, I'm vegetarian.",
    "Wrong, I have two cats.",
]

NOT_CORRECTIONS = [
    "No thanks.",
    "No.",
    "No, that's fine.",
    "Nope, nothing else.",
    "No problem at all!",
    "Actually, can you tell me about tides?",
    "Actually I was wondering about neap tides.",
    "No, I want to talk about something else.",
    "Not bad, you?",
    "I don't know.",
    "That's right!",
    "What's the capital of Portugal?",
    "Is it true that she lives in Lisbon?",
    "Tell me a story about a fox.",
    "Hello there, how are you today?",
    "I love the train ride to Porto.",
]


@pytest.mark.parametrize("text", CORRECTIONS)
def test_corrections_are_heard(text: str) -> None:
    assert sounds_like_correction(text)


@pytest.mark.parametrize("text", NOT_CORRECTIONS)
def test_everything_else_costs_no_model_call(text: str) -> None:
    assert not sounds_like_correction(text)


def test_a_rewrite_in_the_users_words_is_acceptable() -> None:
    assert (
        acceptable(
            "The user's sister Ana lives in Porto.",
            "The user's sister Ana lives in Lisbon.",
            "No, she lives in Lisbon.",
        )
        is None
    )
    assert (
        acceptable(
            "The user drinks coffee every morning.",
            "The user no longer drinks coffee.",
            "I don't drink coffee anymore.",
        )
        is None
    ), "negation words and plural forms are allowed"


def test_what_is_refused() -> None:
    old = "The user's sister Ana lives in Porto."
    assert acceptable(old, "The user's sister Ana lives in Porto.", "No, Porto.") == "unchanged"
    assert (
        acceptable(old, "The user owns a red bicycle.", "No, a red bicycle.") == "different_subject"
    )
    assert (
        acceptable(old, "The user's sister Ana lives in Madrid.", "No, she lives in Lisbon.")
        == "words_the_user_did_not_say"
    ), "the model may rephrase, never add"
    assert (
        acceptable(
            old, "The user's sister Ana does not live in Porto.", "No, I meant the other book."
        )
        == "nothing_said_about_it"
    ), "found live: a small model negates whatever it was shown"


def test_a_one_word_swap_the_user_named_is_acceptable() -> None:
    assert (
        acceptable(
            "The user is vegetarian.", "The user is vegan.", "I'm vegan now, not vegetarian."
        )
        is None
    )
    assert (
        acceptable("The user is 30 years old.", "The user is 31 years old.", "No, I'm 31.") is None
    )
