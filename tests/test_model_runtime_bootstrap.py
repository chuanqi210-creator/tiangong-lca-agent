import unittest


class ModelRuntimeBootstrapTests(unittest.TestCase):
    def test_health_contract_keeps_all_exact_adapter_distributions(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        lock = json.loads(
            (root / "runtime/model-python/source-lock.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(lock["sdk_sources"]),
            {"openai", "google-genai", "anthropic", "zai-sdk"},
        )

    def test_runtime_python_selection_requires_310_and_prefers_first_compatible(self):
        from scripts.bootstrap_model_runtime import select_runtime_python

        versions = {
            "python-old": (3, 9, 18),
            "python-adopted": (3, 11, 9),
            "python-later": (3, 12, 4),
        }
        selected = select_runtime_python(
            list(versions),
            version_probe=lambda command: versions[command],
        )
        self.assertEqual(selected, "python-adopted")

    def test_runtime_python_selection_fails_closed_without_compatible_interpreter(self):
        from scripts.bootstrap_model_runtime import select_runtime_python

        with self.assertRaisesRegex(RuntimeError, "Python 3.10"):
            select_runtime_python(
                ["python-old"],
                version_probe=lambda command: (3, 9, 18),
            )


if __name__ == "__main__":
    unittest.main()
