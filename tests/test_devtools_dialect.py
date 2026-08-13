"""Dialect auto-detection by payload shape (ADR-0047 §6, issue #269): replay
accepts either an Anthropic Messages or an OpenAI Chat Completions payload,
with no client-supplied hint naming which one a bare replay input is.
"""

from blindfold_devtools.dialect import CHAT_COMPLETIONS, MESSAGES, detect_dialect


def test_a_top_level_system_key_is_messages_dialect():
    payload = {"system": "be helpful", "messages": [{"role": "user", "content": "hi"}]}

    assert detect_dialect(payload) == MESSAGES


def test_a_system_role_message_is_chat_completions_dialect():
    payload = {
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hi"},
        ]
    }

    assert detect_dialect(payload) == CHAT_COMPLETIONS


def test_a_function_wrapped_tool_is_chat_completions_dialect():
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
    }

    assert detect_dialect(payload) == CHAT_COMPLETIONS


def test_an_ambiguous_plain_payload_defaults_to_messages_dialect():
    payload = {"messages": [{"role": "user", "content": "hi"}]}

    assert detect_dialect(payload) == MESSAGES
