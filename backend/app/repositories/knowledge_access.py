from sqlalchemy import select, or_
from backend.app.models.knowledge import KnowledgeBase, KnowledgeBaseFile
from backend.app.models.workspace import KnowledgeGrant
from backend.app.models.file import File


def accessible_knowledge(user_id):
    return or_(KnowledgeBase.user_id == user_id, KnowledgeBase.id.in_(select(KnowledgeGrant.knowledge_base_id).where(KnowledgeGrant.user_id == user_id)))


def readable_files(user_id):
    shared = select(KnowledgeBaseFile.file_id).join(KnowledgeBase, KnowledgeBase.id == KnowledgeBaseFile.knowledge_base_id).where(accessible_knowledge(user_id))
    return or_(File.user_id == user_id, File.id.in_(shared))


def independent_files():
    """默认发现只列独立文件的最新版本，旧版仍可由会话附件或版本记录显式查看。"""
    from sqlalchemy.orm import aliased
    from backend.app.models.workspace import DocumentRevision
    newer = aliased(DocumentRevision)
    retired = select(DocumentRevision.file_id).join(newer, (newer.document_id == DocumentRevision.document_id) & (newer.revision > DocumentRevision.revision))
    return ~File.id.in_(select(KnowledgeBaseFile.file_id)) & ~File.id.in_(retired)
