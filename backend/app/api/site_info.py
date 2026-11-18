"""公开的站点展示信息；不得序列化整个运行配置。"""
from fastapi import APIRouter
from pydantic import BaseModel
from backend.app.core.config import settings

router = APIRouter(prefix='/api', tags=['site'])


class SiteInfo(BaseModel):
    site_name: str
    icp_record: str
    icp_record_url: str
    gongan_record: str
    gongan_record_url: str


@router.get('/site-info', response_model=SiteInfo)
def get_site_info():
    # 只生成官方备案查询链接，不接受可执行或外部任意链接。
    police_url = 'https://beian.mps.gov.cn/'
    if settings.gongan_record_code:
        police_url += '#/query/webSearch?code=' + settings.gongan_record_code
    return SiteInfo(site_name=settings.site_name, icp_record=settings.icp_record,
                    icp_record_url='https://beian.miit.gov.cn/' if settings.icp_record else '',
                    gongan_record=settings.gongan_record,
                    gongan_record_url=police_url if settings.gongan_record else '')
