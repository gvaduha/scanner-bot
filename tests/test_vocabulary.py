import unittest

from scanner_bot.vocabulary import VocabularyScorer, tokenize


class TokenizeTest(unittest.TestCase):
    def test_case_and_punctuation(self):
        self.assertEqual(tokenize("Hello, WORLD! It's"), ["hello", "world", "it", "s"])

    def test_cyrillic_and_yo(self):
        self.assertEqual(tokenize("Ёлка, СКИДКА!"), ["елка", "скидка"])


class ScorerTest(unittest.TestCase):
    def test_sum_and_threshold_is_strict(self):
        s = VocabularyScorer({"scam": 3, "money": 2}, threshold=5)
        r = s.score("SCAM: send money now")
        self.assertEqual(r.score, 5)
        self.assertFalse(r.alert)  # 5 is not greater than 5
        self.assertTrue(s.score("scam scam money").alert)  # 3+3+2 = 8

    def test_repeats_off(self):
        s = VocabularyScorer({"scam": 3}, threshold=4, count_repeats=False)
        r = s.score("scam scam scam")
        self.assertEqual(r.score, 3)
        self.assertEqual(r.matches, {"scam": 1})

    def test_whole_words_only(self):
        s = VocabularyScorer({"cat": 1}, threshold=0)
        self.assertEqual(s.score("concatenate category").score, 0)
        self.assertEqual(s.score("a cat.").score, 1)

    def test_prefix(self):
        s = VocabularyScorer({"crypto*": 2, "скидк*": 1}, threshold=0)
        r = s.score("Cryptocurrency and crypto! Скидки и скидка")
        self.assertEqual(r.matches, {"crypto*": 2, "скидк*": 2})
        self.assertEqual(r.score, 6)

    def test_phrase(self):
        s = VocabularyScorer({"free money": 4}, threshold=3)
        self.assertTrue(s.score("Get FREE   money!").alert)
        self.assertFalse(s.score("money is not free").alert)

    def test_phrase_with_prefix(self):
        s = VocabularyScorer({"заработ* онлайн": 5}, threshold=0)
        self.assertEqual(s.score("Заработок онлайн без вложений").score, 5)

    def test_negative_weights(self):
        s = VocabularyScorer({"crypto": 3, "news": -2}, threshold=2)
        self.assertTrue(s.score("crypto").alert)
        self.assertFalse(s.score("crypto news").alert)

    def test_empty_text(self):
        self.assertEqual(VocabularyScorer({"a": 1}, 0).score("").score, 0)


if __name__ == "__main__":
    unittest.main()
