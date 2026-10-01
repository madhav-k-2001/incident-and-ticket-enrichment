import io
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import get_db
from app.db.models import Document


@pytest.fixture
def mock_db_session():
    session = MagicMock()
    return session


@pytest.fixture
def client(mock_db_session):
    app.dependency_overrides[get_db] = lambda: mock_db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_serve_portal(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Document Ingestion Pipeline" in response.text
    assert "Gemini Embedding 2" in response.text


def test_health_check(client, mock_db_session):
    mock_db_session.execute.return_value = True
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "embedding_model" in data
    assert data["embedding_dimension"] == 768


def test_quota_endpoint(client):
    response = client.get("/api/quota")
    assert response.status_code == 200
    data = response.json()
    assert "rpm" in data
    assert "tpm" in data
    assert "rpd" in data


def test_upload_invalid_file_type(client):
    file_content = b"Some executable or invalid data"
    response = client.post(
        "/api/upload", files=[("files", ("malicious.exe", file_content, "application/octet-stream"))]
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_upload_valid_documents(client, mock_db_session):
    pdf_content = b"%PDF-1.4 sample content"
    docx_content = b"PK sample docx content"

    with patch("app.api.endpoints.redis_client") as mock_redis:
        mock_redis.rpush.return_value = 1

        response = client.post(
            "/api/upload",
            files=[
                ("files", ("document1.pdf", pdf_content, "application/pdf")),
                (
                    "files",
                    (
                        "document2.docx",
                        docx_content,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                ),
            ],
        )

        assert response.status_code == 200
        data = response.json()
        assert "Successfully queued 2 document(s)" in data["message"]
        assert len(data["documents"]) == 2
        assert mock_db_session.add.call_count == 2
        assert mock_db_session.commit.call_count == 2


def test_list_documents(client, mock_db_session):
    doc = Document(
        id="test-doc-123",
        filename="report.pdf",
        file_type="pdf",
        file_size=1024,
        file_path="/uploads/test-doc-123_report.pdf",
        status="COMPLETED",
        total_chunks=10,
        processed_chunks=10,
    )

    query_mock = MagicMock()
    query_mock.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [doc]
    query_mock.count.return_value = 1
    mock_db_session.query.return_value = query_mock

    response = client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["documents"]) == 1
    assert data["documents"][0]["filename"] == "report.pdf"
    assert data["documents"][0]["status"] == "COMPLETED"
