import unittest
from unittest.mock import patch

import bitomat_usdt_monitor as monitor


class GoogleFinanceParsingTests(unittest.TestCase):
    def test_usd_pln_parser_rejects_unrelated_one_dollar_value(self) -> None:
        html = '<html><body>Promo text $1 <span>not the FX quote</span></body></html>'

        price = monitor._extract_google_finance_price(
            html,
            minimum=monitor.USD_PLN_MIN,
            maximum=monitor.USD_PLN_MAX,
        )

        self.assertIsNone(price)

    def test_parser_skips_out_of_range_candidates(self) -> None:
        html = '{"price":"1.0000"} ignored junk {"price":"3.6004"}'

        price = monitor._extract_google_finance_price(
            html,
            minimum=monitor.USD_PLN_MIN,
            maximum=monitor.USD_PLN_MAX,
        )

        self.assertEqual(price, 3.6004)

    def test_reference_falls_back_when_google_usd_pln_is_impossible(self) -> None:
        def fake_fetch_text(url: str, *, accept: str | None = None) -> str:
            if url == monitor.GOOGLE_FINANCE_USDT_USD_URL:
                return '<html><body>$1</body></html>'
            if url == monitor.GOOGLE_FINANCE_USD_PLN_URL:
                return '<html><body>$1</body></html>'
            raise AssertionError(f"Unexpected URL: {url}")

        def fake_fetch_json(url: str, *, accept: str | None = None) -> object:
            self.assertEqual(url, monitor.COINGECKO_USDT_PLN_URL)
            return {"tether": {"pln": 3.63}}

        with (
            patch.object(monitor, "fetch_text", side_effect=fake_fetch_text),
            patch.object(monitor, "fetch_json", side_effect=fake_fetch_json),
        ):
            reference_pln, source = monitor.fetch_reference_pln()

        self.assertEqual(reference_pln, 3.63)
        self.assertEqual(source, "CoinGecko fallback")


if __name__ == "__main__":
    unittest.main()
