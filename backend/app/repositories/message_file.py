from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.file import File
from backend.app.models.message_file import MessageFile

class MessageFileError(ValueError):
    """附件不存在或不符合消息附件要求。"""

async def build_attachments(session: AsyncSession, file_ids: list[str]) -> list[MessageFile]:  # 只校验并构造关联对象，由消息保存流程统一提交
    unique_ids = list(dict.fromkeys(file_ids))  # 去重，保留用户选择顺序
    if len(unique_ids) > 3:
        raise MessageFileError("每条消息最多添加3份附件")
    if not unique_ids:
        return []
    statement = select(File.id, File.filename, File.size).where(File.id.in_(unique_ids), File.status == "completed")  # 不读取正文，只允许处理成功的文件
    result = await session.execute(statement)
    files = {row.id: row for row in result.all()}  # 文件 ID 对应的基本信息
    if len(files) != len(unique_ids):
        raise MessageFileError("附件不存在或尚未处理成功，请重新选择")
    attachments = []  # 尚未写入数据库的关联对象
    for position, file_id in enumerate(unique_ids):
        file = files[file_id]
        attachments.append(MessageFile(file_id=file_id, linked_file_id=file_id, filename=file.filename, size=file.size, position=position))
    return attachments
