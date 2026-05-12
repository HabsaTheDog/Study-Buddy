import unittest
from unittest.mock import patch

from uni_agent.study_build.intent import parse_user_intent
from uni_agent.study_build.quiz_permission import quiz_permission_for_intent
from uni_agent.study_build.resource_bundle import build_resource_bundle
from uni_agent.study_build.course_routing import rank_courses
from uni_agent.study_build.resource_tools import classify_resource_role


COURSES = [
    {
        "id": "32897",
        "title": "BMR-VZ-2-SS2026-ET2-DE/165657 Elektrotechnik 2 Knoebl LektorIn: Karl Knöbl Ihre Rolle: TeilnehmerIn",
        "url": "https://moodle.example/course/view.php?id=32897",
    }
]


DOCUMENT_INDEX = {
    "documents": [
        {
            "name": "ComplexNumbers.pdf",
            "path": "data/moodle/materials/bmr-vz-2-ss2026-et2-de-165657-elektrotechnik-2-knoebl-lektorin-karl-knobl-ihre-rolle-teilnehmerin/ComplexNumbers.pdf",
            "suffix": ".pdf",
            "pages": [{"page": 2, "text": "Phasors are complex numbers used for AC circuits with resistors, capacitors and inductors."}],
        },
        {
            "name": "BC546-50_Datenblatt.pdf",
            "path": "data/moodle/materials/bmr-vz-2-ss2026-et2-de-165657-elektrotechnik-2-knoebl-lektorin-karl-knobl-ihre-rolle-teilnehmerin/BC546-50_Datenblatt.pdf",
            "suffix": ".pdf",
            "pages": [{"page": 1, "text": "BC546 transistor datasheet."}],
        },
    ]
}


class StudyBuildIntentTests(unittest.TestCase):
    def test_quiz_style_defaults_to_permission_question(self):
        intent = parse_user_intent(
            "Elektrotechnik 2 zusammenfassen und Theoriefragen wie bei Moodle Quizzes geben",
            quiz_access="ask",
            max_repair_cycles=3,
        )
        permission = quiz_permission_for_intent(intent)
        self.assertTrue(intent.wants_quiz_style)
        self.assertEqual(permission["status"], "needs-user-authorization")
        self.assertFalse(permission["allowed"])

    def test_quiz_access_none_disables_quiz_resources(self):
        intent = parse_user_intent("ET2 Zusammenfassung mit Fragen", quiz_access="none", max_repair_cycles=3)
        permission = quiz_permission_for_intent(intent)
        self.assertEqual(permission["status"], "disabled")
        self.assertFalse(permission["allowed"])


class StudyBuildResourceTests(unittest.TestCase):
    def test_elektrotechnik_2_prompt_prefers_et2_course(self):
        courses = [
            {
                "id": "31038",
                "title": "BMR-VZ-1-WS2025-ET1-DE/159255 Elektrotechnik 1 LektorIn: Julia Bauer Ihre Rolle: TeilnehmerIn",
            },
            {
                "id": "32897",
                "title": "BMR-VZ-2-SS2026-ET2-DE/165657 Elektrotechnik 2 Knoebl LektorIn: Karl Knöbl Ihre Rolle: TeilnehmerIn",
            },
            {
                "id": "33000",
                "title": "BMR-VZ-2-SS2026-ETLB2-DE Elektrotechnik Labor 2 LektorInnen: Bauer",
            },
        ]
        ranked = rank_courses("erstelle eine Formelsammlung für Elektrotechnik 2", courses)
        self.assertIn("ET2", ranked[0]["title"])
        self.assertNotIn("Labor", ranked[0]["title"])

    def test_classifies_datasheets_outside_theory(self):
        self.assertEqual(classify_resource_role("BC546-50_Datenblatt.pdf"), "datasheet")
        self.assertEqual(classify_resource_role("ComplexNumbers.pdf"), "theory")

    def test_bundle_marks_quiz_as_authorization_required_and_does_not_select_datasheet(self):
        intent = parse_user_intent("Elektrotechnik 2 gesamte Theorie mit Moodle Quizfragen", quiz_access="ask", max_repair_cycles=3)
        with patch("uni_agent.study_build.resource_tools.load_synced_courses", return_value=COURSES), patch(
            "uni_agent.study_build.resource_tools.read_json", return_value=DOCUMENT_INDEX
        ):
            bundle, ambiguous = build_resource_bundle(intent)
        self.assertEqual(ambiguous, [])
        statuses = {resource.title: resource.status for resource in bundle.resources}
        self.assertEqual(statuses["Moodle quizzes/tests"], "authorization_required")
        self.assertEqual(statuses["BC546-50_Datenblatt.pdf"], "available_not_used")
        self.assertEqual(len(bundle.source_chunks), 1)


if __name__ == "__main__":
    unittest.main()
