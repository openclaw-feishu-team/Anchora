#!/usr/bin/env python3
# 文件路径：skills/feishu-memory/interactive_cards.py
# 修改类型：新增
# 依赖说明：标准库 + requests
# 长连接方式需安装: pip install lark-oapi
"""
飞书交互式卡片：candidate 记忆确认/驳回。

支持两种回调方式：
1. HTTP 回调（需公网地址或内网穿透）
2. WebSocket 长连接（官方推荐，无需公网地址）

飞书后台配置：
- 事件与回调 → 回调配置 → 选择「使用长连接接收回调」
- 或：选择「将回调发送至开发者服务器」→ 填写公网 URL
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).parent))
from memory import confirm_candidate, db_conn, get_feishu_account, get_openclaw_config, get_tenant_access_token, now_iso, write_audit_log


def build_candidate_card(record: dict) -> dict:
    """构建 candidate 记忆审核卡片。"""
    mem_id = record.get("id", "")
    project = record.get("project", "未分类")
    decision = record.get("decision", "")
    deadline = record.get("deadline") or "无"
    evidence = (record.get("evidence") or record.get("reasoning") or "")[:100]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "yellow",
            "title": {"tag": "plain_text", "content": "待确认的项目决策记忆"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**项目**：{project}\n**决策**：{decision}\n**DDL**：{deadline}"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**证据预览**：{evidence or '无'}"}},
            {"tag": "action", "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅ 确认入库"},
                    "type": "primary",
                    "value": {"memory_id": mem_id, "action": "confirm"},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "❌ 驳回"},
                    "type": "danger",
                    "value": {"memory_id": mem_id, "action": "reject"},
                },
            ]},
        ],
    }


def reject_candidate(mem_id: str, actor: str = "system") -> dict:
    """将 candidate 记忆标记为 rejected。"""
    try:
        conn = db_conn()
        c = conn.cursor()
        c.execute("UPDATE decisions SET status='rejected', updated_at=? WHERE id=? AND status='candidate'", (now_iso(), mem_id))
        changed = c.rowcount
        conn.commit()
        conn.close()
        if changed:
            write_audit_log(mem_id, "reject", actor, "Candidate → Rejected")
            return {"status": "ok", "id": mem_id, "action": "rejected"}
        return {"status": "noop", "id": mem_id, "message": "记录不存在或不是 candidate"}
    except Exception as exc:
        print(f"reject_candidate 失败: {exc}", file=sys.stderr)
        return {"status": "error", "id": mem_id, "error": str(exc)}


def handle_card_action(payload: dict, actor: str = "feishu") -> dict:
    """处理飞书卡片按钮回调。"""
    try:
        value = payload.get("action", {}).get("value") or payload.get("value") or payload
        mem_id = value.get("memory_id") or value.get("id")
        action = value.get("action")
        if not mem_id or action not in {"confirm", "reject"}:
            return {"status": "error", "message": "缺少 memory_id 或 action"}
        if action == "confirm":
            result = confirm_candidate(mem_id, actor=actor)
        else:
            result = reject_candidate(mem_id, actor=actor)
        return {"toast": {"type": "success", "content": f"已处理：{result.get('action', action)}"}, "result": result}
    except Exception as exc:
        print(f"handle_card_action 失败: {exc}", file=sys.stderr)
        return {"status": "error", "error": str(exc)}


def send_candidate_card(chat_id: str, record: dict, account: str = "group") -> dict:
    """向飞书群聊发送 candidate 审核卡片。"""
    if not chat_id:
        return {"status": "skip", "message": "缺少 chat_id"}
    try:
        cfg = get_openclaw_config() or {}
        app_id, app_secret = get_feishu_account(cfg, account)
        token = get_tenant_access_token(app_id, app_secret)
        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(build_candidate_card(record), ensure_ascii=False),
        }
        resp = requests.post(url, headers=headers, params={"receive_id_type": "chat_id"}, json=body, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            return {"status": "ok", "response": data}
        return {"status": "error", "response": data}
    except Exception as exc:
        print(f"send_candidate_card 失败: {exc}", file=sys.stderr)
        return {"status": "error", "error": str(exc)}


class CardCallbackHandler(BaseHTTPRequestHandler):
    """最小 HTTP 回调处理器。"""

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if payload.get("type") == "url_verification":
                result = {"challenge": payload.get("challenge")}
            else:
                result = handle_card_action(payload)
            data = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            print(f"CardCallbackHandler 失败: {exc}", file=sys.stderr)
            self.send_response(500)
            self.end_headers()

    def log_message(self, *_args):
        return


def run_server(port: int = 8787):
    """启动 HTTP 回调服务（需公网地址或内网穿透）。"""
    server = HTTPServer(("0.0.0.0", port), CardCallbackHandler)
    print(f"feishu-memory HTTP callback listening on :{port}")
    print("WARNING: 飞书无法访问 127.0.0.1，请使用内网穿透或改用长连接方式")
    server.serve_forever()


def _get_encrypt_and_verify_tokens(cfg: dict, account: str) -> tuple:
    """从 openclaw.json 获取 encrypt_key 和 verification_token。"""
    feishu_cfg = cfg.get("channels", {}).get("feishu", {})
    accounts = feishu_cfg.get("accounts", {})
    if account in accounts:
        acc = accounts[account]
        return acc.get("encryptKey", ""), acc.get("verificationToken", "")
    return "", ""


def _try_import_sdk():
    """尝试导入 lark-oapi SDK，返回 (WSClient, EventDispatcherHandler, P2CardActionTrigger)"""
    import importlib

    # lark-oapi 1.5.x 的实际模块结构（通过诊断确认）
    try:
        ws_mod = importlib.import_module("lark_oapi.ws")
        WSClient = getattr(ws_mod, "Client")
        disp_mod = importlib.import_module("lark_oapi.event.dispatcher_handler")
        EventDispatcherHandler = getattr(disp_mod, "EventDispatcherHandler")
        p2_mod = importlib.import_module("lark_oapi.event.callback.model.p2_card_action_trigger")
        P2CardActionTrigger = getattr(p2_mod, "P2CardActionTrigger")
        print(f"[WS] 使用 lark-oapi SDK: WS=Client, Dispatcher=EventDispatcherHandler, Model=P2CardActionTrigger")
        return WSClient, EventDispatcherHandler, P2CardActionTrigger
    except Exception as exc:
        print(f"[WS] 导入失败: {exc}", file=sys.stderr)
        # 诊断
        try:
            import lark_oapi
            print(f"[WS] lark_oapi 已安装，顶层 attrs: {[a for a in dir(lark_oapi) if not a.startswith('_')][:20]}", file=sys.stderr)
        except Exception as e2:
            print(f"[WS] import lark_oapi 也失败: {e2}", file=sys.stderr)
        return None, None, None


def run_ws_client(account: str = "group"):
    """
    启动飞书官方 WebSocket 长连接客户端（无需公网地址）。

    飞书后台配置:
      事件与回调 → 回调配置 → 选择「使用长连接接收回调」

    依赖: pip install lark-oapi
    """
    import importlib

    # 导入 SDK
    try:
        ws_mod = importlib.import_module("lark_oapi.ws")
        WSClient = getattr(ws_mod, "Client")
        dh_mod = importlib.import_module("lark_oapi.event.dispatcher_handler")
        EventDispatcherHandler = getattr(dh_mod, "EventDispatcherHandler")
        p2_mod = importlib.import_module("lark_oapi.event.callback.model.p2_card_action_trigger")
        P2CardActionTrigger = getattr(p2_mod, "P2CardActionTrigger")
    except Exception as exc:
        print(f"[ERROR] 导入 lark-oapi 失败: {exc}", file=sys.stderr)
        print("[INFO] 请运行: pip install lark-oapi", file=sys.stderr)
        sys.exit(1)

    cfg = get_openclaw_config() or {}
    app_id, app_secret = get_feishu_account(cfg, account)

    print(f"[WS] 启动长连接 (app_id={app_id[:8]}...)")
    print("[WS] 飞书后台配置: 事件与回调 → 回调配置 → 选择「使用长连接接收回调」")

    # 卡片按钮点击回调函数
    def on_card_action(event) -> dict:
        """处理卡片按钮点击。"""
        try:
            # 从 SDK 事件对象提取数据
            action_value = {}
            operator_name = ""
            operator_open_id = ""

            if hasattr(event, "event") and event.event is not None:
                evt = event.event
                if hasattr(evt, "action") and evt.action is not None:
                    action_obj = evt.action
                    if hasattr(action_obj, "value") and action_obj.value is not None:
                        action_value = action_obj.value
                if hasattr(evt, "operator") and evt.operator is not None:
                    op = evt.operator
                    if hasattr(op, "name"):
                        operator_name = op.name or ""
                    if hasattr(op, "open_id"):
                        operator_open_id = op.open_id or ""

            # 组装成 handle_feishu_event 期望的 dict 格式
            event_dict = {
                "header": {"event_type": "card.action.trigger"},
                "event": {
                    "action": {"value": action_value},
                    "operator": {
                        "name": operator_name,
                        "open_id": operator_open_id,
                    }
                }
            }

            return handle_feishu_event(event_dict)
        except Exception as exc:
            print(f"[WS] 处理卡片事件失败: {exc}", file=sys.stderr)
            return {"toast": {"type": "error", "content": f"处理失败: {exc}"}}

    # 使用 builder 模式创建事件处理器
    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p2_card_action_trigger(on_card_action) \
        .build()

    # 创建并启动 WebSocket 客户端
    client = WSClient(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
    )

    # 在后台线程运行，避免阻塞主线程
    def _start():
        try:
            client.start()
        except Exception as exc:
            print(f"[WS] 客户端异常退出: {exc}", file=sys.stderr)

    t = threading.Thread(target=_start, daemon=True)
    t.start()
    print("[WS] 长连接客户端已启动，等待飞书推送事件...")

    # 主线程保持存活
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[WS] 收到中断信号，正在关闭...")


def _extract_card_action_payload(event_data: dict) -> dict:
    """从飞书长连接事件体中提取卡片 action payload。"""
    # 长连接事件体结构: {"event": {"action": {"value": {...}}, ...}}
    event = event_data.get("event", {})
    action = event.get("action", {})
    return {
        "action": action,
        "value": action.get("value", {}),
        "operator": event.get("operator", {}),
    }


def build_card_response(result: dict, record: dict = None) -> dict:
    """构建飞书卡片回调响应（用于长连接方式返回）。"""
    action_name = result.get("action", "unknown")
    toast = {"type": "success", "content": f"已{action_name}"}
    if result.get("status") == "error":
        toast = {"type": "error", "content": f"处理失败: {result.get('error', '')}"}
    resp = {"toast": toast}
    # 如果已处理，更新卡片状态
    if action_name in ("confirmed", "rejected") and record:
        status_text = "✅ 已确认入库" if action_name == "confirmed" else "❌ 已驳回"
        record["status"] = action_name
        new_card = build_candidate_card(record)
        new_card["header"]["title"]["content"] = f"待确认的项目决策记忆 - {status_text}"
        new_card["elements"][-1] = {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**状态**：{status_text}\n**处理时间**：{now_iso()}"}
        }
        resp["card"] = {"type": "raw", "data": new_card}
    return resp


def handle_feishu_event(event_data: dict) -> dict:
    """
    处理飞书推送的事件（可被 OpenClaw 的 feishu channel 直接调用）。

    支持的事件类型：
    - card.action.trigger : 卡片按钮点击
    - url_verification    : 飞书回调地址验证

    返回飞书要求的响应格式。
    """
    header = event_data.get("header", {})
    event_type = header.get("event_type", "")

    # URL 验证（HTTP 方式首次配置时需要）
    if event_data.get("type") == "url_verification" or event_type == "url_verification":
        return {"challenge": event_data.get("challenge", "")}

    # 卡片按钮点击
    if event_type == "card.action.trigger":
        payload = _extract_card_action_payload(event_data)
        value = payload.get("value", {})
        mem_id = value.get("memory_id") or value.get("id")
        action = value.get("action")
        operator = payload.get("operator", {})
        actor = operator.get("name") or operator.get("open_id") or "feishu_user"

        if not mem_id or action not in {"confirm", "reject"}:
            return {"toast": {"type": "error", "content": "无效的卡片操作"}}

        result = handle_card_action(value, actor=actor)

        # 获取记录用于更新卡片
        record = None
        try:
            conn = db_conn()
            c = conn.cursor()
            c.execute("SELECT * FROM decisions WHERE id=?", (mem_id,))
            row = c.fetchone()
            cols = [d[0] for d in c.description] if c.description else []
            conn.close()
            if row:
                record = {cols[i]: row[i] for i in range(len(cols))}
        except Exception:
            pass

        return build_card_response(result.get("result", result), record)

    return {}


def main():
    parser = argparse.ArgumentParser(description="feishu-memory 飞书交互式卡片")
    sub = parser.add_subparsers(dest="command", required=True)

    p_server = sub.add_parser("server", help="启动 HTTP 回调服务（需公网地址或内网穿透）")
    p_server.add_argument("--port", type=int, default=8787)

    p_ws = sub.add_parser("ws", help="启动 WebSocket 长连接客户端（无需公网地址）")
    p_ws.add_argument("--account", default="group", help="飞书账号名")

    p_reject = sub.add_parser("reject", help="驳回 candidate 记忆")
    p_reject.add_argument("id")
    p_reject.add_argument("--actor", default="admin")

    args = parser.parse_args()
    if args.command == "server":
        run_server(args.port)
    elif args.command == "ws":
        run_ws_client(account=args.account)
    elif args.command == "reject":
        print(json.dumps(reject_candidate(args.id, args.actor), ensure_ascii=False))


if __name__ == "__main__":
    main()
