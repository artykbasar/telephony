import unittest

from telephony.phone_numbers import normalize_phone_number


class PhoneNumberNormalizationTest(unittest.TestCase):
    def test_uk_variants_have_same_e164_key(self):
        expected = "+447700900123"
        for value in ("07700 900123", "07700900123", "+44 7700 900123", "+447700900123", "00447700900123"):
            self.assertEqual(normalize_phone_number(value, "GB"), expected)

    def test_sip_uri_normalizes_to_phone_number(self):
        self.assertEqual(normalize_phone_number('"John" <sip:+447700900123@example.test>', "GB"), "+447700900123")

    def test_short_internal_extensions_are_not_country_normalized(self):
        self.assertEqual(normalize_phone_number("2001", "GB"), "ext:2001")
        self.assertEqual(normalize_phone_number("*97", "GB"), "ext:*97")

    def test_blank_is_not_matchable(self):
        self.assertIsNone(normalize_phone_number("", "GB"))


if __name__ == "__main__":
    unittest.main()
