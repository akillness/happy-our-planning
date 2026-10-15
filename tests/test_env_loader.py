"""scripts/common/env.py — dotenv 파서/로더 테스트 (값 미출력, 순수 동작만)."""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.common.env import load_dotenv, parse_dotenv


class TestParseDotenv(unittest.TestCase):
    def test_basic_and_comments(self):
        parsed = parse_dotenv("# 주석\nKOPIS_KEY=abc123\nEMPTY=\nNOEQUALS\n")
        self.assertEqual(parsed, {"KOPIS_KEY": "abc123", "EMPTY": ""})

    def test_quotes_and_export(self):
        parsed = parse_dotenv('A="a b"\nB=\'c\'\nexport C=d\n')
        self.assertEqual(parsed, {"A": "a b", "B": "c", "C": "d"})

    def test_invalid_names_skipped(self):
        parsed = parse_dotenv("9BAD=x\nHAS-DASH=y\nGOOD_Z=1\n")
        self.assertEqual(parsed, {"GOOD_Z": "1"})


class TestLoadDotenv(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(load_dotenv(Path("/nonexistent/.env")), [])

    def test_existing_env_wins(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text("NOTCHIMA_TEST_KEY=fromfile\n", encoding="utf-8")
            os.environ["NOTCHIMA_TEST_KEY"] = "fromenv"
            try:
                self.assertEqual(load_dotenv(p), [])
                self.assertEqual(os.environ["NOTCHIMA_TEST_KEY"], "fromenv")
            finally:
                del os.environ["NOTCHIMA_TEST_KEY"]

    def test_loads_missing_only(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text("NOTCHIMA_TEST_KEY2=fromfile\n", encoding="utf-8")
            os.environ.pop("NOTCHIMA_TEST_KEY2", None)
            try:
                self.assertEqual(load_dotenv(p), ["NOTCHIMA_TEST_KEY2"])
            finally:
                os.environ.pop("NOTCHIMA_TEST_KEY2", None)


if __name__ == "__main__":
    unittest.main()
