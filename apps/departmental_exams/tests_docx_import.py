import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from django.core.exceptions import PermissionDenied
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService

from .docx_import import QuestionDOCXImportService, QuestionDOCXParser
from .models import FacultyContribution, Question, QuestionImportBatch, QuestionImportRow
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="{main_type}"/>{extra}
</Types>"""
PACKAGE_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>{extra}
</Relationships>"""
DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{relationships}
</Relationships>"""
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
WORDPROCESSINGML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NOTE_PARTS = {
    "footnote": {
        "member_name": "word/footnotes.xml",
        "root_name": "footnotes",
        "record_name": "footnote",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
        "relationship_type": f"{WORD_RELATIONSHIPS}/footnotes",
        "relationship_target": "footnotes.xml",
    },
    "endnote": {
        "member_name": "word/endnotes.xml",
        "root_name": "endnotes",
        "record_name": "endnote",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
        "relationship_type": f"{WORD_RELATIONSHIPS}/endnotes",
        "relationship_target": "endnotes.xml",
    },
}
VALID_QUESTION_PARAGRAPHS = (
    "1. Compatibility check?",
    "A. One",
    "B. Two",
    "C. Three",
    "D. Four",
    "Answer: A",
    "Difficulty: Moderate",
)


def paragraph(text):
    fragments = escape(text).replace("\n", "</w:t><w:br/><w:t xml:space=\"preserve\">")
    return f'<w:p><w:r><w:t xml:space="preserve">{fragments}</w:t></w:r></w:p>'


def word_document(body):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        f'<w:body>{body}<w:sectPr/></w:body></w:document>'
    )


def make_docx(
    paragraphs=(), *, name="questions.docx", body_extra="", content_type=MAIN_TYPE,
    content_types_extra="", rels_extra="", extra_members=None, compression=zipfile.ZIP_DEFLATED,
    document_override=None,
):
    document = document_override or word_document(
        f'{"".join(paragraph(item) for item in paragraphs)}{body_extra}'
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES.format(main_type=content_type, extra=content_types_extra))
        package.writestr("_rels/.rels", PACKAGE_RELS.format(extra=rels_extra))
        package.writestr("word/document.xml", document)
        for member_name, value in extra_members or []:
            package.writestr(member_name, value)
    return SimpleUploadedFile(
        name, stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def default_note_xml(kind, *, extra=""):
    config = NOTE_PARTS[kind]
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<w:{config["root_name"]}
    xmlns:w="{WORDPROCESSINGML}"
    xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
    xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
    mc:Ignorable="w14">
  <w:{config["record_name"]} w:id="-1" w:type="separator">
    <w:p w14:paraId="10000001" w14:textId="77777777" w:rsidR="00112233" w:rsidRDefault="00112233">
      <w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>
      <w:r><w:separator/></w:r>
    </w:p>
  </w:{config["record_name"]}>
  <w:{config["record_name"]} w:id="0" w:type="continuationSeparator">
    <w:p w14:paraId="10000002" w14:textId="77777777" w:rsidR="00112233" w:rsidRDefault="00112233">
      <w:pPr><w:spacing w:after="0"/></w:pPr>
      <w:r><w:continuationSeparator/></w:r>
    </w:p>
  </w:{config["record_name"]}>
  {extra}
</w:{config["root_name"]}>'''


def note_content_type_override(kind, *, member_name=None, content_type=None):
    config = NOTE_PARTS[kind]
    return (
        f'<Override PartName="/{member_name or config["member_name"]}" '
        f'ContentType="{content_type or config["content_type"]}"/>'
    )


def note_relationship(kind, *, relationship_id="rId1", target=None, rel_type=None):
    config = NOTE_PARTS[kind]
    id_attribute = (
        "" if relationship_id is None else f' Id="{relationship_id}"'
    )
    return (
        f'<Relationship{id_attribute} '
        f'Type="{rel_type or config["relationship_type"]}" '
        f'Target="{target or config["relationship_target"]}"/>'
    )


def make_note_docx(
    note_parts,
    *,
    paragraphs=VALID_QUESTION_PARAGRAPHS,
    document_override=None,
    relationship_kinds=None,
    content_type_kinds=None,
    relationships_override=None,
    content_types_extra="",
):
    relationship_kinds = (
        tuple(note_parts)
        if relationship_kinds is None
        else tuple(relationship_kinds)
    )
    content_type_kinds = (
        tuple(note_parts)
        if content_type_kinds is None
        else tuple(content_type_kinds)
    )
    content_types_extra = "".join(
        note_content_type_override(kind)
        for kind in content_type_kinds
    ) + content_types_extra
    relationships = relationships_override if relationships_override is not None else "".join(
        note_relationship(kind, relationship_id=f"rId{index + 1}")
        for index, kind in enumerate(relationship_kinds)
    )
    members = [
        (NOTE_PARTS[kind]["member_name"], xml)
        for kind, xml in note_parts.items()
    ]
    if relationships:
        members.append((
            "word/_rels/document.xml.rels",
            DOCUMENT_RELS.format(relationships=relationships),
        ))
    return make_docx(
        paragraphs,
        content_types_extra=content_types_extra,
        extra_members=members,
        document_override=document_override,
    )


class DOCXParserTests(SimpleTestCase):
    def assert_file_rejected(self, upload, text=None):
        parsed = QuestionDOCXParser.parse(upload)
        self.assertGreater(parsed.error_count, 0)
        self.assertEqual(parsed.rows[0].row_number, 1)
        self.assertEqual(parsed.data_rows, [])
        if text:
            self.assertIn(text, parsed.rows[0].errors[0]["message"])

    def test_accepts_default_footnote_separator_metadata_only(self):
        parsed = QuestionDOCXParser.parse(make_note_docx({
            "footnote": default_note_xml("footnote"),
        }))
        self.assertEqual(parsed.error_count, 0)
        self.assertEqual(len(parsed.data_rows), 1)

    def test_accepts_default_endnote_separator_metadata_only(self):
        parsed = QuestionDOCXParser.parse(make_note_docx({
            "endnote": default_note_xml("endnote"),
        }))
        self.assertEqual(parsed.error_count, 0)
        self.assertEqual(len(parsed.data_rows), 1)

    def test_accepts_both_default_footnote_and_endnote_separator_metadata(self):
        parsed = QuestionDOCXParser.parse(make_note_docx({
            "footnote": default_note_xml("footnote"),
            "endnote": default_note_xml("endnote"),
        }))
        self.assertEqual(parsed.error_count, 0)
        self.assertEqual(len(parsed.data_rows), 1)

    def test_rejects_actual_faculty_authored_footnote(self):
        actual_note = (
            '<w:footnote w:id="1"><w:p><w:r>'
            '<w:footnoteRef/><w:t>Faculty note</w:t>'
            '</w:r></w:p></w:footnote>'
        )
        self.assert_file_rejected(
            make_note_docx({
                "footnote": default_note_xml("footnote", extra=actual_note),
            }),
            "Actual footnote text",
        )

    def test_rejects_actual_faculty_authored_endnote(self):
        actual_note = (
            '<w:endnote w:id="1"><w:p><w:r>'
            '<w:endnoteRef/><w:t>Faculty note</w:t>'
            '</w:r></w:p></w:endnote>'
        )
        self.assert_file_rejected(
            make_note_docx({
                "endnote": default_note_xml("endnote", extra=actual_note),
            }),
            "Actual endnote text",
        )

    def test_rejects_footnote_and_endnote_references_from_document_content(self):
        for kind, reference_name, message in (
            ("footnote", "footnoteReference", "Footnotes"),
            ("endnote", "endnoteReference", "Endnotes"),
        ):
            with self.subTest(kind=kind):
                body = (
                    f'<w:p><w:r><w:t>1. Compatibility</w:t>'
                    f'<w:{reference_name} w:id="1"/></w:r></w:p>'
                    + "".join(paragraph(item) for item in VALID_QUESTION_PARAGRAPHS[1:])
                )
                self.assert_file_rejected(
                    make_note_docx(
                        {kind: default_note_xml(kind)},
                        document_override=word_document(body),
                    ),
                    message,
                )

    def test_rejects_malformed_and_unexpected_note_part_content(self):
        malformed = f'<w:footnotes xmlns:w="{WORDPROCESSINGML}"><w:footnote'
        self.assert_file_rejected(
            make_note_docx({"footnote": malformed}),
            "unsafe or malformed XML in word/footnotes.xml",
        )

        unexpected_record = (
            '<w:footnote w:id="1" w:type="continuationNotice">'
            '<w:p><w:r><w:continuationSeparator/></w:r></w:p>'
            '</w:footnote>'
        )
        self.assert_file_rejected(
            make_note_docx({
                "footnote": default_note_xml("footnote", extra=unexpected_record),
            }),
            "non-default footnote records",
        )

        unexpected_negative_id = default_note_xml("footnote").replace(
            'w:id="-1"',
            'w:id="-2"',
            1,
        )
        self.assert_file_rejected(
            make_note_docx({"footnote": unexpected_negative_id}),
            "non-default footnote records",
        )

        unexpected_semantics = default_note_xml("endnote").replace(
            "<w:separator/>",
            "<w:drawing/>",
            1,
        )
        self.assert_file_rejected(
            make_note_docx({"endnote": unexpected_semantics}),
            "unexpected semantic content",
        )

    def test_rejects_note_parts_without_exact_content_type_and_relationship(self):
        footnotes = {"footnote": default_note_xml("footnote")}
        self.assert_file_rejected(
            make_note_docx(footnotes, content_type_kinds=()),
            "standard content type",
        )
        self.assert_file_rejected(
            make_note_docx(footnotes, relationship_kinds=()),
            "exactly one standard internal relationship",
        )

    def test_rejects_official_note_content_types_on_alternate_parts(self):
        authored_footnote = default_note_xml(
            "footnote",
            extra=(
                '<w:footnote w:id="1"><w:p><w:r>'
                '<w:footnoteRef/><w:t>Faculty note</w:t>'
                '</w:r></w:p></w:footnote>'
            ),
        )
        for kind, member_name, xml in (
            ("footnote", "word/notes/footnotes.xml", authored_footnote),
            ("endnote", "word/notes/endnotes.xml", default_note_xml("endnote")),
        ):
            with self.subTest(kind=kind, member_name=member_name):
                self.assert_file_rejected(
                    make_docx(
                        VALID_QUESTION_PARAGRAPHS,
                        content_types_extra=note_content_type_override(
                            kind,
                            member_name=member_name,
                        ),
                        extra_members=[(member_name, xml)],
                    ),
                    "only on its canonical part",
                )

    def test_rejects_official_note_content_type_supplied_by_default(self):
        default_declaration = (
            '<Default Extension="fnote" '
            f'ContentType="{NOTE_PARTS["footnote"]["content_type"]}"/>'
        )
        self.assert_file_rejected(
            make_docx(
                VALID_QUESTION_PARAGRAPHS,
                content_types_extra=default_declaration,
                extra_members=[
                    ("customXml/renamed.fnote", default_note_xml("footnote")),
                ],
            ),
            "only on its canonical part",
        )

    def test_rejects_case_altered_canonical_note_part_path(self):
        member_name = "WORD/footnotes.xml"
        self.assert_file_rejected(
            make_docx(
                VALID_QUESTION_PARAGRAPHS,
                content_types_extra=note_content_type_override(
                    "footnote",
                    member_name=member_name,
                ),
                extra_members=[(member_name, default_note_xml("footnote"))],
            ),
            "non-standard note-part name",
        )

    def test_rejects_note_relationships_to_alternate_targets(self):
        for kind, target in (
            ("footnote", "notes/footnotes.xml"),
            ("endnote", "notes/endnotes.xml"),
        ):
            with self.subTest(kind=kind):
                self.assert_file_rejected(
                    make_note_docx(
                        {kind: default_note_xml(kind)},
                        relationships_override=note_relationship(kind, target=target),
                    ),
                    "standard internal relationship",
                )

    def test_rejects_duplicate_relationship_ids_before_semantic_use(self):
        relationships = (
            note_relationship("footnote", relationship_id="rId1")
            + note_relationship("endnote", relationship_id="rId1")
        )
        self.assert_file_rejected(
            make_note_docx(
                {
                    "footnote": default_note_xml("footnote"),
                    "endnote": default_note_xml("endnote"),
                },
                relationships_override=relationships,
            ),
            "duplicate relationship Id",
        )

    def test_rejects_missing_and_blank_relationship_ids(self):
        for relationship_id in (None, ""):
            with self.subTest(relationship_id=relationship_id):
                self.assert_file_rejected(
                    make_note_docx(
                        {"footnote": default_note_xml("footnote")},
                        relationships_override=note_relationship(
                            "footnote",
                            relationship_id=relationship_id,
                        ),
                    ),
                    "valid non-empty Id",
                )

    def test_rejects_case_altered_official_note_relationship_types(self):
        for kind, suffix in (("footnote", "FOOTNOTES"), ("endnote", "Endnotes")):
            with self.subTest(kind=kind):
                altered_type = f"{WORD_RELATIONSHIPS}/{suffix}"
                self.assert_file_rejected(
                    make_note_docx(
                        {kind: default_note_xml(kind)},
                        relationships_override=note_relationship(
                            kind,
                            rel_type=altered_type,
                        ),
                    ),
                    "exact standard case-sensitive type",
                )

    def test_rejects_duplicate_and_conflicting_content_type_overrides(self):
        canonical_override = note_content_type_override("footnote")
        conflicting_override = note_content_type_override(
            "footnote",
            content_type="application/xml",
        )
        for duplicate in (canonical_override, conflicting_override):
            with self.subTest(duplicate=duplicate):
                self.assert_file_rejected(
                    make_note_docx(
                        {"footnote": default_note_xml("footnote")},
                        content_types_extra=duplicate,
                    ),
                    "duplicate content-type Override",
                )

    def test_rejects_malformed_content_type_part_name(self):
        self.assert_file_rejected(
            make_docx(
                VALID_QUESTION_PARAGRAPHS,
                content_types_extra=note_content_type_override(
                    "footnote",
                    member_name="word/notes/../footnotes.xml",
                ),
            ),
            "malformed content-type PartName",
        )

    def test_rejects_duplicate_semantic_note_relationships(self):
        relationships = (
            note_relationship("footnote", relationship_id="rId1")
            + note_relationship("footnote", relationship_id="rId2")
        )
        self.assert_file_rejected(
            make_note_docx(
                {"footnote": default_note_xml("footnote")},
                relationships_override=relationships,
            ),
            "exactly one standard internal relationship",
        )

    def test_valid_one_and_multiple_questions_preserve_unicode_spaces_and_lines(self):
        parsed = QuestionDOCXParser.parse(make_docx([
            "1. What  is π?\nKeep this line.", "A. 3", "B. 3.  14", "C. 4", "D. 5", "Answer: B", "Difficulty: Moderate",
            "2) Ano ang résumé?", "A) Isa", "B) Dalawa", "C) Tatlo", "D) Apat", "Answer: A", "Difficulty: Easy",
        ]))
        self.assertEqual(parsed.error_count, 0)
        self.assertEqual(len(parsed.data_rows), 2)
        self.assertEqual(parsed.data_rows[0].payload["question_text"], "What  is π?\nKeep this line.")
        self.assertEqual(parsed.data_rows[0].payload["choice_b"], "3.  14")
        self.assertEqual(parsed.data_rows[1].payload["question_text"], "Ano ang résumé?")

    def test_rejects_symbol_font_run_instead_of_silently_dropping_it(self):
        body = (
            '<w:p><w:r><w:t xml:space="preserve">1. Identify </w:t>'
            '<w:sym w:font="Symbol" w:char="F070"/><w:t>.</w:t></w:r></w:p>'
            + "".join(paragraph(item) for item in (
                "A. Alpha", "B. Beta", "C. Gamma", "D. Delta",
                "Answer: A", "Difficulty: Easy",
            ))
        )
        self.assert_file_rejected(
            make_docx(document_override=word_document(body)),
            "symbol-font characters",
        )

    def test_preserves_no_break_hyphen_as_unicode(self):
        body = (
            '<w:p><w:r><w:t>1. non</w:t><w:noBreakHyphen/>'
            '<w:t>breaking</w:t></w:r></w:p>'
            + "".join(paragraph(item) for item in (
                "A. Alpha", "B. Beta", "C. Gamma", "D. Delta",
                "Answer: A", "Difficulty: Easy",
            ))
        )
        parsed = QuestionDOCXParser.parse(
            make_docx(document_override=word_document(body))
        )
        self.assertEqual(parsed.error_count, 0)
        self.assertEqual(parsed.data_rows[0].payload["question_text"], "non\u2011breaking")

    def test_preserves_soft_hyphen_as_unicode(self):
        body = (
            '<w:p><w:r><w:t>1. co</w:t><w:softHyphen/>'
            '<w:t>operate</w:t></w:r></w:p>'
            + "".join(paragraph(item) for item in (
                "A. Alpha", "B. Beta", "C. Gamma", "D. Delta",
                "Answer: A", "Difficulty: Easy",
            ))
        )
        parsed = QuestionDOCXParser.parse(
            make_docx(document_override=word_document(body))
        )
        self.assertEqual(parsed.error_count, 0)
        self.assertEqual(parsed.data_rows[0].payload["question_text"], "co\u00adoperate")

    def test_preserves_meaningful_blank_paragraph_without_spurious_questions(self):
        parsed = QuestionDOCXParser.parse(make_docx([
            "1. First line", "", "Second line", "",
            "A. Alpha", "", "B. Beta", "C. Gamma", "D. Delta",
            "Answer: A", "Difficulty: Easy",
        ]))
        self.assertEqual(parsed.error_count, 0)
        self.assertEqual(len(parsed.data_rows), 1)
        self.assertEqual(
            parsed.data_rows[0].payload["question_text"],
            "First line\n\nSecond line",
        )
        self.assertEqual(parsed.data_rows[0].payload["choice_a"], "Alpha")

    def test_preserves_continuation_paragraph_indentation(self):
        parsed = QuestionDOCXParser.parse(make_docx([
            "1. Evaluate the code:", "    x = 1", "A. Alpha", "B. Beta",
            "C. Gamma", "D. Delta", "Answer: A", "Difficulty: Easy",
        ]))
        self.assertEqual(parsed.error_count, 0)
        self.assertEqual(
            parsed.data_rows[0].payload["question_text"],
            "Evaluate the code:\n    x = 1",
        )

    def test_rejects_unrecognized_semantic_run_content(self):
        body = (
            '<w:p><w:r><w:t>1. Page </w:t><w:ptab/><w:t> marker</w:t></w:r></w:p>'
            + "".join(paragraph(item) for item in (
                "A. Alpha", "B. Beta", "C. Gamma", "D. Delta",
                "Answer: A", "Difficulty: Easy",
            ))
        )
        self.assert_file_rejected(
            make_docx(document_override=word_document(body)),
            "unsupported semantic inline element",
        )

    def test_missing_answer_and_difficulty_create_editable_invalid_rows(self):
        parsed = QuestionDOCXParser.parse(make_docx([
            "1. Missing metadata?", "A. One", "B. Two", "C. Three", "D. Four",
        ]))
        self.assertEqual(len(parsed.data_rows), 1)
        fields = {item["field"] for item in parsed.data_rows[0].errors}
        self.assertTrue({"correct_answer", "difficulty"}.issubset(fields))
        self.assertEqual(parsed.data_rows[0].payload["question_text"], "Missing metadata?")

    def test_shared_answer_difficulty_and_choice_validation_remains_authoritative(self):
        parsed = QuestionDOCXParser.parse(make_docx([
            "1. Invalid fields", "A. Same", "B. Same", "C. Three", "D. Four",
            "Answer: A,B", "Difficulty: Expert",
        ]))
        fields = {item["field"] for item in parsed.data_rows[0].errors}
        self.assertTrue({"choices", "correct_answer", "difficulty"}.issubset(fields))

    def test_numeric_academic_text_is_not_over_stripped(self):
        parsed = QuestionDOCXParser.parse(make_docx([
            "1. Evaluate 3.14 + 2.0.", "A. 5.14", "B. 4", "C. 3", "D. 2", "Answer: A", "Difficulty: Difficult",
        ]))
        self.assertEqual(parsed.data_rows[0].payload["question_text"], "Evaluate 3.14 + 2.0.")
        self.assert_file_rejected(
            make_docx(["2026. is a year, not a question number."]),
            "Each question must begin",
        )

    def test_rejects_non_docx_and_non_zip(self):
        for name in ("questions.doc", "questions.docm", "questions.dotx", "questions.dotm", "questions.rtf", "questions.odt", "questions.pdf"):
            with self.subTest(name=name):
                self.assert_file_rejected(SimpleUploadedFile(name, b"not-word"), ".docx")
        self.assert_file_rejected(SimpleUploadedFile("renamed.docx", b"not-a-zip"), "ZIP/OPC")

    def test_rejects_macro_vba_activex_ole_and_external_relationships(self):
        macro = "application/vnd.ms-word.document.macroEnabled.main+xml"
        self.assert_file_rejected(make_docx([], content_type=macro), "Macro-enabled")
        for member in ("word/vbaProject.bin", "word/activeX/activeX1.bin", "word/embeddings/oleObject1.bin", "word/media/image1.png"):
            with self.subTest(member=member):
                self.assert_file_rejected(make_docx([], extra_members=[(member, b"x")]), "not supported")
        external = '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.test" TargetMode="External"/>'
        self.assert_file_rejected(make_docx([], rels_extra=external), "External relationships")

    def test_rejects_tables_drawings_equations_altchunk_and_automatic_lists(self):
        structures = {
            "tables": '<w:tbl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
            "drawings": '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:drawing/></w:p>',
            "equations": '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:oMath/></w:p>',
            "altChunk": '<w:altChunk xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
            "Automatically": '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:pPr><w:numPr/></w:pPr></w:p>',
        }
        for message, xml in structures.items():
            with self.subTest(message=message):
                self.assert_file_rejected(make_docx([], body_extra=xml), message)

    def test_rejects_duplicate_traversal_unsupported_compression_and_zip_bomb_ratio(self):
        duplicate = make_docx([], extra_members=[("word/document.xml", b"duplicate")])
        self.assert_file_rejected(duplicate, "duplicate")
        self.assert_file_rejected(make_docx([], extra_members=[("../escape.xml", b"x")]), "unsafe member path")
        self.assert_file_rejected(make_docx([], compression=zipfile.ZIP_BZIP2), "unsupported ZIP compression")
        self.assert_file_rejected(make_docx([], extra_members=[("word/large.bin", b"A" * (1024 * 1024))]), "compression ratio")

    def test_rejects_encrypted_member_flag(self):
        raw = bytearray(make_docx(["1. Q", "A. A", "B. B", "C. C", "D. D", "Answer: A", "Difficulty: Easy"]).read())
        local = raw.find(b"PK\x03\x04")
        central = raw.find(b"PK\x01\x02")
        raw[local + 6] |= 1
        raw[central + 8] |= 1
        self.assert_file_rejected(SimpleUploadedFile("encrypted.docx", bytes(raw)), "Encrypted")

    def test_rejects_xml_dtd_entity_and_end_answer_key(self):
        hostile = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "unsafe">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&e;</w:t></w:r></w:p></w:body></w:document>'
        self.assert_file_rejected(make_docx([], document_override=hostile), "unsafe or malformed XML")
        self.assert_file_rejected(make_docx(["Answer Key:", "1. B"]), "Answer keys")


class DOCXImportServiceTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("docx-faculty")
        self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get()
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_DOCX_IMPORT_ENABLED_KEY,
            True, tenant_id=self.tenant.id, value_type="BOOL", is_active=True,
        )

    def upload(self, paragraphs):
        return make_docx(paragraphs)

    def preview(self, paragraphs):
        return QuestionDOCXImportService.create_preview(
            contribution_id=self.contribution.id, uploaded_file=self.upload(paragraphs),
            user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )

    @staticmethod
    def valid_paragraphs(number=1, text="Imported Word question"):
        return [f"{number}. {text}", "A. Alpha", "B. Beta", "C. Gamma", "D. Delta", "Answer: B", "Difficulty: Moderate"]

    def test_feature_defaults_off_and_direct_service_fails_closed(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_DOCX_IMPORT_ENABLED_KEY,
            False, tenant_id=self.tenant.id, value_type="BOOL", is_active=True,
        )
        with self.assertRaises(PermissionDenied):
            self.preview(self.valid_paragraphs())

    def test_staged_correction_revalidates_and_duplicate_warnings(self):
        batch = self.preview(["1. Needs fixing", "A. Same", "B. Same", "C. Three", "D. Four"])
        self.assertEqual(batch.status, QuestionImportBatch.Status.INVALID)
        payload = {
            "question_text": "Needs fixing", "choice_a": "One", "choice_b": "Two",
            "choice_c": "Three", "choice_d": "Four", "correct_answer": "A",
            "difficulty": "EASY",
        }
        batch, row = QuestionDOCXImportService.update_staged_row(
            token=batch.token, row_number=2, payload=payload, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        self.assertEqual(batch.status, QuestionImportBatch.Status.READY)
        self.assertEqual(row.errors, [])

    def test_import_uses_docx_provenance_positions_revision_cleanup_and_remains_draft(self):
        paragraphs = self.valid_paragraphs(1, "First") + self.valid_paragraphs(2, "Second")
        batch = self.preview(paragraphs)
        revision_before = self.contribution.revision
        confirmed, changed = QuestionDOCXImportService.confirm(
            token=batch.token, expected_file_sha256=batch.file_sha256, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
        )
        self.contribution.refresh_from_db()
        questions = list(self.contribution.questions.order_by("position"))
        self.assertTrue(changed)
        self.assertEqual(confirmed.source_format, QuestionImportBatch.SourceFormat.DOCX)
        self.assertEqual([item.position for item in questions], [1, 2])
        self.assertEqual({item.entry_method for item in questions}, {Question.EntryMethod.DOCX})
        self.assertEqual(self.contribution.revision, revision_before + 1)
        self.assertEqual(self.contribution.status, FacultyContribution.Status.DRAFT)
        self.assertFalse(QuestionImportRow.objects.filter(batch=batch).exists())
        replay, changed_again = QuestionDOCXImportService.confirm(
            token=batch.token, expected_file_sha256=batch.file_sha256, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
        )
        self.assertEqual(replay.pk, batch.pk)
        self.assertFalse(changed_again)
        self.assertEqual(Question.objects.count(), 2)

    def test_wrong_owner_tenant_and_campus_fail_closed(self):
        batch = self.preview(self.valid_paragraphs())
        other = self.make_faculty("other-docx-faculty")
        with self.assertRaises(Http404):
            QuestionDOCXImportService.owner_batch(token=batch.token, user=other, tenant_id=self.tenant.id)
        with self.assertRaises(Http404):
            QuestionDOCXImportService.owner_batch(token=batch.token, user=self.faculty, tenant_id=self.tenant.id + 999)
        with self.assertRaises(PermissionDenied):
            QuestionDOCXImportService.confirm(
                token=batch.token, expected_file_sha256=batch.file_sha256, user=self.faculty,
                tenant_id=self.tenant.id, campus_id=self.campus.id + 999,
            )

    def test_quota_and_stale_revision_are_revalidated(self):
        batch = self.preview(self.valid_paragraphs())
        self.contribution.revision += 1
        self.contribution.save(update_fields=["revision"])
        with self.assertRaises(Exception):
            QuestionDOCXImportService.confirm(
                token=batch.token, expected_file_sha256=batch.file_sha256, user=self.faculty,
                tenant_id=self.tenant.id, campus_id=self.campus.id,
            )
        self.assertFalse(Question.objects.filter(import_batch=batch).exists())

    def test_deadline_closed_exempt_and_submitted_states_deny_docx_preview(self):
        cases = ("deadline", "closed", "exempt", "submitted")
        for case in cases:
            with self.subTest(case=case):
                if case == "deadline":
                    type(self.configuration).objects.filter(pk=self.configuration.pk).update(
                        contribution_deadline=timezone.now() - timezone.timedelta(minutes=1)
                    )
                elif case == "closed":
                    type(self.configuration).objects.filter(pk=self.configuration.pk).update(
                        workflow_status=self.configuration.WorkflowStatus.CLOSED,
                        closed_at=timezone.now(),
                    )
                elif case == "exempt":
                    type(self.parent).objects.filter(pk=self.parent.pk).update(
                        inclusion_status=self.parent.InclusionStatus.EXEMPT
                    )
                else:
                    FacultyContribution.objects.filter(pk=self.contribution.pk).update(
                        status=FacultyContribution.Status.SUBMITTED,
                        submitted_at=timezone.now(),
                    )
                with self.assertRaises(PermissionDenied):
                    self.preview(self.valid_paragraphs())
                # Restore the fixture for the next subcase without invoking workflow services.
                type(self.configuration).objects.filter(pk=self.configuration.pk).update(
                    contribution_deadline=timezone.now() + timezone.timedelta(days=1),
                    workflow_status=self.configuration.WorkflowStatus.OPEN,
                    closed_at=None,
                )
                type(self.parent).objects.filter(pk=self.parent.pk).update(
                    inclusion_status=self.parent.InclusionStatus.INCLUDED
                )
                FacultyContribution.objects.filter(pk=self.contribution.pk).update(
                    status=FacultyContribution.Status.DRAFT,
                    submitted_at=None,
                )
                self.configuration.refresh_from_db()
                self.parent.refresh_from_db()
                self.contribution.refresh_from_db()

    def test_faculty_upload_review_fix_import_ui_and_feature_off_direct_denial(self):
        self.client.force_login(self.faculty)
        workspace_url = reverse(
            "departmental_exams:contribution_workspace", args=[self.contribution.id]
        )
        workspace = self.client.get(workspace_url)
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "Import from Word (.docx)")

        upload_url = reverse("departmental_exams:docx_upload", args=[self.contribution.id])
        upload_page = self.client.get(upload_url)
        self.assertEqual(upload_page.status_code, 200)
        for hook in (
            "data-question-upload-form", "data-import-mode=\"docx\"",
            "data-docx-submit", "data-docx-progress-panel", "role=\"status\"",
            "aria-live=\"polite\"",
        ):
            self.assertContains(upload_page, hook)
        self.assertContains(upload_page, "Processing Word questionnaire...")
        invalid = self.client.post(upload_url, {
            "expected_contribution_revision": self.contribution.revision,
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, "This field is required", status_code=400)
        self.assertContains(invalid, "data-docx-progress-panel", status_code=400)
        script = Path(settings.BASE_DIR, "static", "js", "departmental_exam_csv_import.js").read_text(encoding="utf-8")
        for source_contract in (
            "if (uploading) return", "control.disabled = active",
            "Checking file safety...", "Reading questions...",
            "Validating extracted questions...", "Preparing preview...",
            "progress.removeAttribute(\"aria-valuenow\")",
        ):
            self.assertIn(source_contract, script)

        outsider = self.make_faculty("docx-ui-outsider")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(upload_url).status_code, 404)
        self.client.force_login(self.faculty)
        response = self.client.post(upload_url, {
            "expected_contribution_revision": self.contribution.revision,
            "docx_file": self.upload(["1. Fix me", "A. One", "B. Two", "C. Three", "D. Four"]),
        })
        self.assertEqual(response.status_code, 302)
        batch = QuestionImportBatch.objects.get(source_format=QuestionImportBatch.SourceFormat.DOCX)
        preview = self.client.get(reverse("departmental_exams:docx_preview", args=[batch.token]))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Step 2")
        edit = self.client.post(reverse("departmental_exams:docx_row_edit", args=[batch.token, 2]), {
            "expected_contribution_revision": self.contribution.revision,
            "question_text": "Fix me", "choice_a": "One", "choice_b": "Two",
            "choice_c": "Three", "choice_d": "Four", "correct_answer": "A",
            "difficulty": "EASY",
        })
        self.assertEqual(edit.status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QuestionImportBatch.Status.READY)
        confirmed = self.client.post(reverse("departmental_exams:docx_confirm", args=[batch.token]), {
            "file_sha256": batch.file_sha256,
        })
        self.assertRedirects(confirmed, workspace_url)
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.status, FacultyContribution.Status.DRAFT)

        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_DOCX_IMPORT_ENABLED_KEY,
            False, tenant_id=self.tenant.id, value_type="BOOL", is_active=True,
        )
        self.assertEqual(self.client.get(upload_url).status_code, 403)
        self.assertNotContains(self.client.get(workspace_url), "Import from Word (.docx)")
