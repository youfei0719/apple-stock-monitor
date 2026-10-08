"""到货购买指南（静态内容）。"""

from fastapi import APIRouter

router = APIRouter(prefix="/guide", tags=["guide"])


@router.get("/purchase")
def purchase_guide():
    return {
        "title": "到货购买指南",
        "steps": [
            "收到有货通知后，尽快点击通知中的直达链接打开商品页。",
            "选择「到店取货」，确认门店为通知中的门店，下单并完成支付。",
            "Apple Store 方案：直接在 Apple Store App 中搜索机型，下单时选择到店取货。",
            "小程序直达：复制 part number（如 MJYC4CH/A），在官网搜索框粘贴直达商品页。",
            "热门机型放货极快，建议提前登录 Apple ID 并绑好支付方式。",
            "若点进已无货：属于「一闪而过」库存，监控会继续盯，下一轮有货会再通知。",
        ],
    }
