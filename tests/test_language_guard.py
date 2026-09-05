"""Tests for LLM language-switching guards."""

from __future__ import annotations

import unittest

from prompt_matrix.services.language_guard import (
    LANGUAGE_SYSTEM_INSTRUCTION,
    append_language_instruction,
    build_messages_with_language_guard,
    ensure_response_language,
    get_language_instruction,
    guard_messages,
    is_json_response_mode,
    locale_to_language_name,
    set_request_locale,
)


class LanguageGuardTests(unittest.TestCase):
    def tearDown(self):
        set_request_locale(None)

    def test_locale_to_language_name_all_seven(self):
        self.assertEqual(locale_to_language_name("en"), "English")
        self.assertEqual(locale_to_language_name("es"), "Spanish")
        self.assertEqual(locale_to_language_name("zh"), "Chinese")
        self.assertEqual(locale_to_language_name("fr"), "French")
        self.assertEqual(locale_to_language_name("de"), "German")
        self.assertEqual(locale_to_language_name("ja"), "Japanese")
        self.assertEqual(locale_to_language_name("tr"), "Turkish")

    def test_append_language_instruction_appends_rule(self):
        base = "You are Assure."
        out = append_language_instruction(base, "tr")
        self.assertTrue(out.startswith("You are Assure."))
        self.assertIn("Turkish", out)
        self.assertIn("HARD LANGUAGE RULE", out)
        self.assertIn("Her zaman Türkçe yanıt ver.", out)

    def test_append_language_instruction_empty_system(self):
        out = append_language_instruction("", "fr")
        self.assertIn("Réponds toujours en français.", out)
        self.assertIn(LANGUAGE_SYSTEM_INSTRUCTION.format(language="French"), out)

    def test_get_language_instruction_all_seven(self):
        self.assertEqual(get_language_instruction("en"), "Always respond in English.")
        self.assertEqual(get_language_instruction("tr"), "Her zaman Türkçe yanıt ver.")
        self.assertEqual(get_language_instruction("es"), "Responde siempre en español.")
        self.assertEqual(get_language_instruction("fr"), "Réponds toujours en français.")
        self.assertEqual(get_language_instruction("de"), "Antworte immer auf Deutsch.")
        self.assertEqual(get_language_instruction("ja"), "常に日本語で応答してください。")
        self.assertEqual(get_language_instruction("zh"), "请始终用中文回复。")
        self.assertEqual(get_language_instruction("unknown"), "Always respond in English.")

    def test_append_language_instruction_idempotent(self):
        once = append_language_instruction("You are Assure.", "tr")
        twice = append_language_instruction(once, "tr")
        self.assertEqual(once, twice)
        self.assertEqual(once.count("HARD LANGUAGE RULE"), 1)

    def test_build_messages_with_language_guard(self):
        msgs = build_messages_with_language_guard("System base.", "User question.", locale="de")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("German", msgs[0]["content"])
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(msgs[1]["content"], "User question.")

    def test_guard_messages_updates_existing_system(self):
        original = [
            {"role": "system", "content": "Base system."},
            {"role": "user", "content": "Hello"},
        ]
        guarded = guard_messages(original, locale="ja")
        self.assertIn("Japanese", guarded[0]["content"])
        self.assertEqual(guarded[1]["content"], "Hello")
        self.assertNotIn("Japanese", original[0]["content"])

    def test_guard_messages_prepends_when_no_system(self):
        guarded = guard_messages([{"role": "user", "content": "Hi"}], locale="es")
        self.assertEqual(guarded[0]["role"], "system")
        self.assertIn("Spanish", guarded[0]["content"])
        self.assertEqual(guarded[1]["content"], "Hi")

    def test_guard_messages_skip_json_mode(self):
        msgs = [{"role": "user", "content": "Extract JSON"}]
        self.assertEqual(
            guard_messages(msgs, locale="tr", skip=True),
            msgs,
        )

    def test_is_json_response_mode(self):
        self.assertTrue(is_json_response_mode({"response_format": {"type": "json_object"}}))
        self.assertFalse(is_json_response_mode({"response_format": {"type": "text"}}))
        self.assertFalse(is_json_response_mode(None))

    def test_ensure_response_language_passthrough(self):
        text = "Merhaba dünya."
        self.assertEqual(ensure_response_language(text, "tr"), text)

    def test_context_locale_override(self):
        set_request_locale("fr")
        guarded = guard_messages([{"role": "user", "content": "Bonjour"}])
        self.assertIn("French", guarded[0]["content"])


if __name__ == "__main__":
    unittest.main()
