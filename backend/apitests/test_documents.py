"""The uploaded-document corpus: store, parsers, endpoints. No embedding model loaded;
names cannot escape the directory, and a rejected upload changes nothing."""

import io
import os
import tempfile
import unittest
import zipfile

from aws_resource_audit.errors import AuditError

from . import _REPO  # noqa: F401  (sys.path fixup)
from .api_base import ApiTestCase
from app.agent.documents import DocumentStore, check_name
from app.agent.loaders import extract_text


class NameTests(unittest.TestCase):
    def test_accepts_an_ordinary_name(self):
        self.assertEqual(check_name("Account Inventory (2024).xlsx"),
                         "Account Inventory (2024).xlsx")

    def test_traversal_is_reduced_to_its_basename(self):
        self.assertEqual(check_name("../../etc/notes.md"), "notes.md")

    def test_a_bare_traversal_has_no_usable_basename(self):
        with self.assertRaises(AuditError):
            check_name("../..")

    def test_an_unknown_extension_is_refused_by_name(self):
        with self.assertRaises(AuditError) as caught:
            check_name("architecture.pages")
        self.assertIn("architecture.pages", str(caught.exception))

    def test_an_extensionless_name_is_refused(self):
        with self.assertRaises(AuditError):
            check_name("README")


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = DocumentStore(os.path.join(tmp.name, "documents"))

    def test_an_absent_directory_is_an_empty_corpus(self):
        self.assertEqual(self.store.files(), [])
        self.assertEqual(self.store.list(), [])

    def test_save_then_list(self):
        self.store.save("notes.md", b"# Notes")
        entry, = self.store.list()
        self.assertEqual(entry["name"], "notes.md")
        self.assertEqual(entry["size"], len("# Notes"))

    def test_saving_the_same_name_replaces_and_says_so(self):
        _, replaced = self.store.save("notes.md", b"first")
        self.assertFalse(replaced)
        _, replaced = self.store.save("notes.md", b"second")
        self.assertTrue(replaced)
        self.assertEqual(len(self.store.list()), 1)
        with open(self.store.path("notes.md")) as f:
            self.assertEqual(f.read(), "second")

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(AuditError):
            self.store.save("notes.md", b"")

    def test_delete_reports_whether_it_found_anything(self):
        self.store.save("notes.md", b"x")
        self.assertTrue(self.store.delete("notes.md"))
        self.assertFalse(self.store.delete("notes.md"))

    def test_a_stray_file_of_an_unknown_type_is_not_in_the_corpus(self):
        """A file dropped in by hand with an unsupported extension is ignored."""
        os.makedirs(self.store.directory, exist_ok=True)
        with open(os.path.join(self.store.directory, "notes.pages"), "w") as f:
            f.write("x")
        self.assertEqual(self.store.files(), [])


class LoaderTests(unittest.TestCase):
    """The formats whose fixtures need no third-party library."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, content, mode="w"):
        path = os.path.join(self.tmp.name, name)
        with open(path, mode) as f:
            f.write(content)
        return path

    def test_markdown_is_read_verbatim(self):
        path = self._write("notes.md", "# Payments\n\nOwned by platform.")
        self.assertIn("Owned by platform.", extract_text(path))

    def test_a_csv_row_becomes_one_line_of_fields(self):
        path = self._write("inventory.csv", "id,owner\nbucket-a,platform\n")
        self.assertEqual(extract_text(path), "id | owner\nbucket-a | platform")

    def test_undecodable_bytes_do_not_lose_the_document(self):
        path = self._write("notes.txt", b"owner: platform \xff\xfe", mode="wb")
        self.assertIn("owner: platform", extract_text(path))

    def test_a_corrupt_file_names_itself_in_the_error(self):
        # A .docx is a zip; this one is not, which is what any truncated or
        # mislabelled upload looks like.
        path = self._write("runbook.docx", b"not a zip at all", mode="wb")
        with self.assertRaises(AuditError) as caught:
            extract_text(path)
        self.assertIn("runbook.docx", str(caught.exception))

    def test_an_empty_docx_parses_to_nothing_rather_than_raising(self):
        path = os.path.join(self.tmp.name, "empty.docx")
        _write_minimal_docx(path)
        self.assertEqual(extract_text(path).strip(), "")


def _write_minimal_docx(path):
    """The smallest thing python-docx will open: a zip with the parts it
    requires and an empty body."""
    content_types = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>")
    rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
        'openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')
    document = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
        'openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)


class ChunkingTests(unittest.TestCase):
    """What gets embedded. No Chroma and no embedding model - _load_and_chunk
    is the part of rag.py that decides content, and it is pure."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = DocumentStore(os.path.join(tmp.name, "documents"))

    def _chunks(self):
        from app.agent.rag import _load_and_chunk

        return _load_and_chunk(self.store.files())

    def test_an_account_id_is_masked_before_it_is_embedded(self):
        """The real account id never reaches the vectors, only its placeholder."""
        self.store.save("notes.md", b"Owned by payments. Account 123456789012.")
        text = "\n".join(c.page_content for c in self._chunks())
        self.assertNotIn("123456789012", text)
        self.assertIn("ACCOUNT-", text)

    def test_a_chunk_carries_the_document_name_not_its_path(self):
        self.store.save("notes.md", b"x")
        self.assertEqual(self._chunks()[0].metadata["source"], "notes.md")

    def test_one_unreadable_document_does_not_cost_the_others(self):
        self.store.save("notes.md", b"keep me")
        self.store.save("broken.pdf", b"not really a pdf")
        with self.assertLogs("pypdf", "WARNING"):
            sources = {c.metadata["source"] for c in self._chunks()}
        self.assertEqual(sources, {"notes.md"})

class FakeIndex:
    """Enough of a Chroma collection for rebuild_index: what it holds, and
    whether it was emptied before it was refilled."""

    def __init__(self):
        self.documents = []
        self.deleted = []

    def get(self):
        return {"ids": [str(i) for i in range(len(self.documents))]}

    def delete(self, ids):
        self.deleted.append(list(ids))
        self.documents = []

    def add_documents(self, documents):
        self.documents.extend(documents)


class RebuildTests(unittest.TestCase):
    """rebuild_index against a fake collection: a rebuild REPLACES, so deleted
    documents stop being searchable."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = DocumentStore(os.path.join(tmp.name, "documents"))
        self.index = FakeIndex()

    def _rebuild(self):
        from app.agent.rag import rebuild_index

        return rebuild_index(self.index, self.store)

    def test_an_empty_corpus_indexes_nothing_and_deletes_nothing(self):
        self.assertEqual(self._rebuild(), 0)
        self.assertEqual(self.index.deleted, [])

    def test_a_rebuild_indexes_every_document(self):
        self.store.save("notes.md", b"payments runbook")
        self.store.save("owners.csv", b"id,owner\nbucket-a,platform\n")
        self.assertEqual(self._rebuild(), 2)
        self.assertEqual({d.metadata["source"] for d in self.index.documents},
                         {"notes.md", "owners.csv"})

    def test_a_deleted_document_is_gone_after_the_next_rebuild(self):
        self.store.save("notes.md", b"payments runbook")
        self._rebuild()
        self.store.delete("notes.md")
        self.assertEqual(self._rebuild(), 0)
        self.assertTrue(self.index.deleted)      # the old chunks were cleared
        self.assertEqual(self.index.documents, [])

    def test_a_rebuild_replaces_rather_than_appends(self):
        self.store.save("notes.md", b"first")
        self._rebuild()
        self._rebuild()
        self.assertEqual(len(self.index.documents), 1)


class DocumentsApiTests(ApiTestCase):
    """The endpoints, against a real temp data directory."""

    def _upload(self, *files):
        return self.client.post(
            "/api/agent/documents",
            files=[("files", (name, io.BytesIO(body), "text/plain"))
                   for name, body in files])

    def test_the_corpus_starts_empty(self):
        response = self.client.get("/api/agent/documents")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_upload_then_list(self):
        response = self._upload(("notes.md", b"# Payments"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([d["name"] for d in body["documents"]], ["notes.md"])
        self.assertEqual(body["replaced"], [])
        # Nothing has been indexed yet, which must not read as "zero chunks".
        self.assertIsNone(body["documents"][0]["chunks"])

    def test_a_replacement_is_reported(self):
        self._upload(("notes.md", b"first"))
        body = self._upload(("notes.md", b"second")).json()
        self.assertEqual(body["replaced"], ["notes.md"])
        self.assertEqual(len(body["documents"]), 1)

    def test_one_bad_file_stores_none_of_the_batch(self):
        response = self._upload(("good.md", b"keep me"),
                                ("bad.pages", b"reject me"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("bad.pages", response.json()["detail"])
        self.assertEqual(self.client.get("/api/agent/documents").json(), [])

    def test_the_same_name_twice_in_one_request_is_refused(self):
        response = self._upload(("notes.md", b"a"), ("notes.md", b"b"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("notes.md", response.json()["detail"])

    def test_delete(self):
        self._upload(("notes.md", b"x"))
        self.assertEqual(
            self.client.delete("/api/agent/documents/notes.md").status_code, 204)
        self.assertEqual(self.client.get("/api/agent/documents").json(), [])

    def test_deleting_something_absent_is_a_404(self):
        self.assertEqual(
            self.client.delete("/api/agent/documents/ghost.md").status_code, 404)

    def test_reindex_rebuilds_and_returns_the_listing(self):
        """The endpoint's own job, with the embedding pass stubbed out: it
        must call the rebuild and answer with what is indexed afterwards."""
        from app.api import documents as route

        calls = []
        original = route.index.rebuild
        route.index.rebuild = lambda: calls.append(True)
        self.addCleanup(setattr, route.index, "rebuild", original)

        self._upload(("notes.md", b"# Payments"))
        response = self.client.post("/api/agent/documents/reindex")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual([d["name"] for d in response.json()], ["notes.md"])

    def test_reindex_reports_a_failure_rather_than_a_200(self):
        """A rebuild that cannot run - no embedding model, unreadable index
        directory - must not look like a rebuild that found nothing."""
        from app.api import documents as route

        def boom():
            raise AuditError("the embedding model could not be loaded.")

        original = route.index.rebuild
        route.index.rebuild = boom
        self.addCleanup(setattr, route.index, "rebuild", original)

        response = self.client.post("/api/agent/documents/reindex")
        self.assertEqual(response.status_code, 400)
        self.assertIn("embedding model", response.json()["detail"])

    def test_documents_land_on_the_data_volume(self):
        """Uploads land on the data volume, not beside the code."""
        self._upload(("notes.md", b"x"))
        self.assertTrue(os.path.isfile(
            os.path.join(self.data_dir, "documents", "notes.md")))


if __name__ == "__main__":
    unittest.main()
