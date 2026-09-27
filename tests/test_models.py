from knowledge_api.models import AuditEvent, Document, Membership, Workspace


def test_models_have_explicit_tenant_fields() -> None:
    assert "workspace_id" in Document.__table__.columns
    assert "workspace_id" in Membership.__table__.columns
    assert "workspace_id" in AuditEvent.__table__.columns
    assert "deleted_at" in Workspace.__table__.columns
