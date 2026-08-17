import unittest

from core.registry import load_all
from api.platforms import get_platforms


class PlatformListingTests(unittest.TestCase):
    def test_cursor_and_tavily_are_exposed_to_management_ui(self):
        load_all()
        names = {item["name"] for item in get_platforms()}

        self.assertIn("cursor", names)
        self.assertIn("tavily", names)


if __name__ == "__main__":
    unittest.main()
