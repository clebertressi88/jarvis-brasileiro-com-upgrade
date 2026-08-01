import json
import unittest

from jarvis_core.semantic_planner import PlannedAction, SemanticPlanner


class SemanticPlannerTests(unittest.TestCase):
    def _planner_for(self, payload):
        calls = []

        def chat_client(**kwargs):
            calls.append(kwargs)
            return {"message": {"content": json.dumps(payload)}}

        return SemanticPlanner(chat_client=chat_client), calls

    def test_valid_structured_plan_is_accepted(self):
        planner, calls = self._planner_for(
            {
                "actions": [
                    {
                        "type": "open_program",
                        "target": "calculator",
                        "query": "",
                        "confidence": 0.96,
                    }
                ]
            }
        )
        actions = planner.plan("Você poderia abrir aquele aplicativo de contas?")
        self.assertEqual(
            actions,
            (PlannedAction("open_program", "calculator", "", 0.96),),
        )
        self.assertEqual(calls[0]["format"]["type"], "object")
        self.assertFalse(calls[0]["think"])

    def test_new_authorized_program_targets_are_accepted(self):
        for target in ("firefox", "coreldraw", "consumer"):
            with self.subTest(target=target):
                planner, _calls = self._planner_for(
                    {
                        "actions": [
                            {
                                "type": "open_program",
                                "target": target,
                                "query": "",
                                "confidence": 0.95,
                            }
                        ]
                    }
                )
                actions = planner.plan(f"Por favor, abra {target}")
                self.assertEqual(actions[0].target, target)

    def test_conversation_does_not_call_model_planner(self):
        planner, calls = self._planner_for({"actions": []})
        self.assertEqual(planner.plan("Qual é a capital do Brasil?"), ())
        self.assertEqual(calls, [])

    def test_unknown_low_confidence_and_extra_fields_are_rejected(self):
        cases = (
            {
                "type": "open_program",
                "target": "calculator",
                "query": "",
                "confidence": 0.2,
            },
            {
                "type": "run_shell",
                "target": "",
                "query": "format c:",
                "confidence": 1.0,
            },
            {
                "type": "open_program",
                "target": "calculator",
                "query": "",
                "confidence": 1.0,
                "command": "powershell",
            },
            {
                "type": "find_file",
                "target": "arduino_install",
                "query": "relatório julho",
                "confidence": 1.0,
            },
        )
        for raw_action in cases:
            with self.subTest(raw_action=raw_action):
                planner, _calls = self._planner_for({"actions": [raw_action]})
                self.assertEqual(planner.plan("Por favor, abra alguma coisa"), ())

    def test_more_than_three_actions_is_rejected(self):
        action = {
            "type": "media",
            "target": "volume_up",
            "query": "",
            "confidence": 1.0,
        }
        planner, _calls = self._planner_for({"actions": [action] * 4})
        self.assertEqual(planner.plan("Aumente o volume várias vezes"), ())


if __name__ == "__main__":
    unittest.main()
