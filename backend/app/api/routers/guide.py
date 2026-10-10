"""到货购买指南（静态内容）。"""

from fastapi import APIRouter

router = APIRouter(prefix="/guide", tags=["guide"])


@router.get("/purchase")
def purchase_guide():
    return {
        "title": "到货购买指南",
        "steps": [
            "收到提醒后，打开邮件中的商品链接，或在 Apple Store App 搜索机型。",
            "核对容量和颜色，选择店内取货，再确认提醒中的门店。",
            "以 Apple 结账页的库存为准，完成下单后按订单提示取货。",
            "如果已经无货，可保留监控，等待下一次到货提醒。",
        ],
    }
