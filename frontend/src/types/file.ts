export interface FileInfo { // 对应后端 FileResponse，仅包含基本信息
  id: string // 文件唯一 ID
  reused?: boolean // 同账号同内容上传复用已有资料
  index_current?: boolean // 当前正文与成功索引的内容一致
  filename: string // 原始文件名
  content_type: string | null // 请求声明的文件类型
  size: number // 实际大小，单位字节
  status: 'processing' | 'completed' | 'failed' // 文件处理状态
  error: string | null // 处理失败说明
  index_status: 'pending' | 'indexing' | 'ready' | 'failed' // 向量索引状态，与文字处理分开
  index_error: string | null // 索引失败说明
  index_key: string | null // 成功索引使用的向量配置
  index_busy: boolean // 等待或计算中的任务，期间不可删除或重复建立索引
  created_at: number // 创建时间戳
  updated_at: number // 更新时间戳
}
export interface FileContent { // 单独查询提取正文时返回的结构
  id: string // 文件 ID
  text_content: string // 提取出的文字
}
export interface MessageAttachment { // 消息中的附件快照，不包含正文和磁盘路径
  file_id: string // 后端文件 ID
  filename: string // 发送时的文件名
  size: number // 文件大小，单位字节
  deleted?: boolean // 原文件删除后仍保留名称、大小和历史ID，兼容旧响应
}
