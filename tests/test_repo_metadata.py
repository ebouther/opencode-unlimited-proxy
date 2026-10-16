#!/usr/bin/env python3
"""Structural checks on the repository's own metadata.

An issue template missing its opening `---` is not an error GitHub reports: the
file renders as an ordinary blank issue with no prompt, so the template silently
stops existing. A README link to a renamed file renders as plain text pointing
nowhere. Both fail quietly, which is the worst way for either to fail.

These checks parse YAML front matter by hand rather than importing pyyaml,
because the test suite must keep working on a machine with nothing but the
standard library.

Run: python3 -m unittest discover -s tests -v
"""

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)


def front_matter(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(text)
    if not match:
        raise AssertionError(
            f"{path}: no closed YAML front matter block "
            "(it must open with '---' on line 1 and close with '---' on its own line)"
        )
    return match.group(1)


class IssueTemplates(unittest.TestCase):
    def test_every_template_has_usable_front_matter(self):
        templates = sorted((REPO / ".github" / "ISSUE_TEMPLATE").glob("*.md"))
        self.assertTrue(templates, "no issue templates found")
        for template in templates:
            with self.subTest(template=template.name):
                fm = front_matter(template)
                for key in ("name:", "description:", "body:"):
                    self.assertIn(key, fm, f"{template.name}: missing {key}")

    def test_templates_are_not_empty_placeholders(self):
        # A template with no fields asks for less than a blank issue would.
        for template in sorted((REPO / ".github" / "ISSUE_TEMPLATE").glob("*.md")):
            with self.subTest(template=template.name):
                self.assertGreaterEqual(front_matter(template).count("- type:"), 3)

    def test_blank_issues_are_disabled_only_with_a_config(self):
        config = REPO / ".github" / "ISSUE_TEMPLATE" / "config.yml"
        self.assertTrue(config.exists(), "no config.yml alongside the templates")
        self.assertIn("blank_issues_enabled: false", config.read_text(encoding="utf-8"))

    def test_the_contact_links_point_at_this_repository(self):
        config = (REPO / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(encoding="utf-8")
        # A stale fork URL in the chooser sends people to the wrong tracker.
        self.assertIn("ebouther/opencode-unlimited-proxy", config)


class PullRequestTemplate(unittest.TestCase):
    def test_exists_and_asks_for_the_thing_that_matters(self):
        path = REPO / ".github" / "PULL_REQUEST_TEMPLATE.md"
        self.assertTrue(path.exists(), "no PR template")
        text = path.read_text(encoding="utf-8")
        for section in ("What this changes", "Why", "How it was verified", "Checklist"):
            self.assertIn(section, text, f"PR template is missing the {section!r} section")

    def test_asks_for_measurement_strength(self):
        # The repository's whole value depends on claims being labelled with how
        # strong they are. A PR form that does not ask makes that opt-in, and
        # opt-in is how unlabelled claims get in.
        text = (REPO / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        for option in ("Measured", "Inferred", "Speculative"):
            self.assertIn(option, text, f"PR template does not offer the {option!r} strength")


class ReadmeLinksResolve(unittest.TestCase):
    """Relative links in the README point at files that exist."""

    def test_relative_markdown_links_resolve(self):
        readme = REPO / "README.md"
        links = set(re.findall(r"\]\((?!https?://|#)([^)]+\.md)\)", readme.read_text(encoding="utf-8")))
        self.assertTrue(links, "no relative links found -- is the README empty?")
        for link in sorted(links):
            with self.subTest(link=link):
                self.assertTrue((REPO / link).exists(), f"README links to missing {link}")


class GeneratedFileIsNotHandEdited(unittest.TestCase):
    """Docs must not tell a reader to edit docker-compose.yml.

    The compose file is generated and CI fails when it drifts from
    `generate-compose.py`, so an instruction to delete services from it by hand
    sends someone down a path that cannot end in a green check. The correct move
    is to start only the services they need.
    """

    DOCS = ["README.md", "CONTRIBUTING.md", "SECURITY.md",
            "docs/free-tier-gates.md", "docs/gate-timeline.md"]

    FORBIDDEN = re.compile(
        r"(?:delete|drop|remove|edit)[^.\n]{0,80}docker-compose\.yml",
        re.IGNORECASE,
    )

    def test_no_doc_asks_the_reader_to_edit_the_compose_file(self):
        offenders = []
        for doc in self.DOCS:
            path = REPO / doc
            if not path.exists():
                continue
            for match in self.FORBIDDEN.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{doc}: ...{match.group(0)[-70:]}")
        self.assertFalse(offenders, "hand-edit instructions found:\n  " + "\n  ".join(offenders))

    def test_the_documented_escape_hatch_is_the_real_one(self):
        # The replacement has to be present too, or the check above just means
        # nobody was told anything.
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("config-init opencode-engine", readme)

    def test_generated_is_labelled_as_generated_in_the_file_itself(self):
        head = (REPO / "stack" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertTrue(
            head.startswith("# GENERATED BY stack/generate-compose.py"),
            "the first line of docker-compose.yml is what stops a hand edit "
            "from looking legitimate to someone opening the file",
        )


class DocumentationAgreesWithTheTests(unittest.TestCase):
    """The README states a test count. Keep it honest."""

    def test_stated_test_count_matches_reality(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        stated = set(re.findall(r"(\d+) tests", readme))
        self.assertTrue(stated, "README does not state a test count")
        for count in stated:
            self.assertEqual(
                int(count), count_suite(),
                f"README says {count} tests; the suite actually runs {count_suite()}",
            )


def count_suite() -> int:
    """Count the tests the suite would run.

    `TestLoader.countTestCases` is gone in Python 3.14, so walk the suite
    instead. A generator counts as one test, not zero.
    """
    def walk(suite) -> int:
        total = 0
        for item in suite:
            if isinstance(item, (list, tuple)) or hasattr(item, "__iter__"):
                try:
                    total += walk(item)
                    continue
                except TypeError:
                    pass
            total += 1
        return total

    loader = unittest.TestLoader()
    return walk(loader.discover(str(REPO / "tests")))


if __name__ == "__main__":
    unittest.main()