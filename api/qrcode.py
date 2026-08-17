"""
二维码支付 API
"""

import os
import time
from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# 存储支付订单信息（实际项目中应该使用数据库）
payment_orders = {}


class QRCodeRequest(BaseModel):
    """二维码请求"""
    amount: float
    order_id: str
    merchant_name: str = "OpenAI 自动注册系统"
    expire_minutes: int = 30


class QRCodeResponse(BaseModel):
    """二维码响应"""
    order_id: str
    amount: float
    merchant_name: str
    qr_code_url: str
    expire_time: str
    created_at: str


@router.post("/generate-qrcode", response_model=QRCodeResponse)
async def generate_qrcode(req: QRCodeRequest):
    """生成支付二维码"""
    order_id = req.order_id or f"ORDER_{int(time.time())}"
    
    # 计算过期时间
    expire_time = datetime.now().timestamp() + (req.expire_minutes * 60)
    
    # 构建二维码数据（这里可以根据实际需求调整）
    qr_data = {
        "app_id": "wx1234567890",  # 示例应用 ID
        "order_id": order_id,
        "amount": req.amount,
        "merchant": req.merchant_name,
        "timestamp": int(time.time()),
        "expire": int(expire_time),
    }
    
    # 将二维码数据转换为 URL 格式（实际使用时可以使用 qrcode 库生成真实的二维码）
    import json
    import base64
    qr_code_url = f"weixin://wxpay/bizpayurl?pr={base64.urlsafe_b64encode(json.dumps(qr_data, ensure_ascii=False).encode()).decode()}"
    
    # 保存订单信息
    payment_orders[order_id] = {
        "amount": req.amount,
        "merchant_name": req.merchant_name,
        "qr_code_url": qr_code_url,
        "expire_time": expire_time,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    
    return QRCodeResponse(
        order_id=order_id,
        amount=req.amount,
        merchant_name=req.merchant_name,
        qr_code_url=qr_code_url,
        expire_time=datetime.fromtimestamp(expire_time).isoformat(),
        created_at=datetime.now().isoformat(),
    )


@router.get("/payment-status/{order_id}")
async def get_payment_status(order_id: str):
    """查询支付状态"""
    if order_id not in payment_orders:
        return {"error": "订单不存在"}
    
    order = payment_orders[order_id]
    
    # 检查是否过期
    if time.time() > order["expire_time"]:
        order["status"] = "expired"
    
    return {
        "order_id": order_id,
        "status": order["status"],
        "amount": order["amount"],
        "created_at": order["created_at"],
    }
