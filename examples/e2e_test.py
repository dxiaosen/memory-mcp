"""端到端全路径验证脚本。

用法（需先启动 Server）:
    python examples/e2e_test.py

该脚本不依赖 memory_mcp_agent，直接用 httpx 调 MCP 工具。
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

URL = "http://127.0.0.1:8765/mcp"
TOKEN_S1 = "3CZBOju3mMv-4s4fb-IjzOn9C4wsVW2-0CuNFtcp72M"  # subject-001, team=research-dept
TOKEN_S2 = "RoGKNRNjFTJE15qFcJTdaMFJ3DQvlAnBwvg54-rpI_M"   # subject-002, no team
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

_passed = 0
_failed = 0


def call(token: str, tool: str, args: dict) -> dict:
    """调 MCP 工具，返回 result payload。"""
    resp = httpx.post(
        URL,
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
        timeout=120,
    )
    body = resp.json()
    result = body.get("result", {})
    sc = result.get("structuredContent", {})
    payload = sc.get("result", sc)
    if payload.get("ok") is False:
        raise RuntimeError(f"tool error: {payload.get('error_code', 'unknown')} - {payload.get('message','')}")
    return payload


def capture(token: str, event_id: str, conv: str, turn: str, user_input: str,
            observed_at: str | None = None) -> dict:
    """用 capture_completed_turn 捕获一个轮次。固定 observed_at 以支持幂等测试。"""
    # 用当前时间减 1 小时确保 valid_from <= now，不会因时区差导致记忆在未来生效
    ts = observed_at or (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return call(token, "capture_completed_turn", {
        "event_id": event_id,
        "contract_version": "1",
        "conversation_id": conv,
        "turn_id": turn,
        "observed_at": ts,
        "messages": [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": f"已处理：{user_input}"},
        ],
    })


def recall(token: str, query: str, profile: str = "investment-research") -> dict:
    return call(token, "recall_memory", {"query": query, "profile_id": profile})


def list_memories(token: str) -> list:
    return call(token, "list_memories", {}).get("items", [])


def list_pending(token: str) -> list:
    return call(token, "list_pending_reviews", {}).get("items", [])


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    status = "✅" if condition else "❌"
    if condition:
        _passed += 1
    else:
        _failed += 1
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


def run():
    print("\n=== 1. auto-save 捕获 + 召回 + owner 隔离 ===")
    r = capture(TOKEN_S1, "e2e-1", "e2e-1", "e2e-1-1",
                "投研周报默认使用中文撰写，列出支持证据和反方风险")
    mem_ids = r.get("created_memory_ids", [])
    check("auto-save 产生记忆", len(mem_ids) == 1, f"created={mem_ids}")

    r = recall(TOKEN_S1, "投研周报", "investment-research")
    check("subject-001 能召回", len(r.get("items", [])) >= 1)

    r = recall(TOKEN_S2, "投研周报", "investment-research")
    check("subject-002 不可见", len(r.get("items", [])) == 0, "owner 隔离")

    print("\n=== 2. pending → confirm（写个人 owner）===")
    r = capture(TOKEN_S1, "e2e-2", "e2e-2", "e2e-2-1",
                "我觉得新能源行业未来应该挺有前景的，可能值得重点关注")
    pending = list_pending(TOKEN_S1)
    if pending:
        review_id = pending[0]["review_id"]
        r = call(TOKEN_S1, "confirm_pending_memory", {"review_id": review_id})
        mem = r.get("memory", {})
        check("confirm 写个人 owner", mem.get("owner_id", "").startswith("tenant-001:subject-001"),
              f"owner={mem.get('owner_id')}")
        check("confirm 后 pending 减少", len(list_pending(TOKEN_S1)) < len(pending))
    else:
        check("pending 产生", False, "模型未返回 pending")

    print("\n=== 3. pending → reject ===")
    # 用 assistant 推断触发 system_inference → pending
    r = capture(TOKEN_S1, "e2e-3", "e2e-3", "e2e-3-1",
                "根据初步分析，某公司估值偏低，建议关注", )
    pending = list_pending(TOKEN_S1)
    if not pending:
        # 再试一条更模糊的
        r = capture(TOKEN_S1, "e2e-3b", "e2e-3b", "e2e-3b-1",
                    "可能大概需要关注一下消费电子的复苏趋势吧")
        pending = list_pending(TOKEN_S1)
    if pending:
        review_id = pending[0]["review_id"]
        r = call(TOKEN_S1, "reject_pending_memory", {"review_id": review_id})
        check("reject 返回 rejected", r.get("status") == "rejected")
        check("reject 后不可召回", len(recall(TOKEN_S1, "消费电子").get("items", [])) == 0)
    else:
        check("pending 产生（reject 路径）", False, "模型两次都未返回 pending")

    print("\n=== 4. pending → promote_to_team ===")
    r = capture(TOKEN_S1, "e2e-4", "e2e-4", "e2e-4-1",
                "大概可能要关注一下半导体国产替代的逻辑")
    pending = list_pending(TOKEN_S1)
    if pending:
        review_id = pending[0]["review_id"]
        r = call(TOKEN_S1, "confirm_pending_memory", {
            "review_id": review_id,
            "promote_to_team": "research-dept",
        })
        mem = r.get("memory", {})
        team_owner = "tenant-001:team:research-dept"
        check("promote_to_team 写团队 owner", mem.get("owner_id") == team_owner,
              f"owner={mem.get('owner_id')}")
        # 团队成员可见
        r = recall(TOKEN_S1, "半导体", "investment-research")
        team_items = [i for i in r.get("items", []) if i.get("owner_id") == team_owner]
        check("团队成员召回团队记忆", len(team_items) >= 1)
        # 非成员不可见
        r = recall(TOKEN_S2, "半导体", "investment-research")
        check("非成员不可见团队记忆", len(r.get("items", [])) == 0)
    else:
        check("pending 产生（promote 路径）", False, "模型未返回 pending")

    print("\n=== 5. 同事件幂等重放 ===")
    # 固定 observed_at 确保 payload 完全一致（用过去时间避免时区问题）
    fixed_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r1 = capture(TOKEN_S1, "e2e-5", "e2e-5", "e2e-5-1",
                 "测试幂等的长期偏好", observed_at=fixed_ts)
    r2 = capture(TOKEN_S1, "e2e-5", "e2e-5", "e2e-5-1",
                 "测试幂等的长期偏好", observed_at=fixed_ts)
    check("重放返回 replayed=true", r2.get("replayed") is True, f"replayed={r2.get('replayed')}")
    check("重放 capture_id 一致", r1.get("capture_id") == r2.get("capture_id"),
          f"id1={r1.get('capture_id','')[:8]} id2={r2.get('capture_id','')[:8]}")

    print("\n=== 6. 同事件不同 payload → conflict ===")
    try:
        r3 = capture(TOKEN_S1, "e2e-5", "e2e-5", "e2e-5-1",
                     "不同的内容导致冲突", observed_at=fixed_ts)
        check("不同 payload 应冲突", False, "未报错")
    except RuntimeError as e:
        check("不同 payload → idempotency_conflict", "conflict" in str(e).lower(), str(e))

    print("\n=== 7. revoke 记忆 ===")
    mems = list_memories(TOKEN_S1)
    # 找一条 active 的
    target = None
    for m in mems:
        if m.get("lifecycle_status") == "active":
            target = m
            break
    if target:
        r = call(TOKEN_S1, "revoke_memory", {"memory_id": target["memory_id"]})
        mem = r.get("memory", {})
        check("revoke 返回 revoked", mem.get("lifecycle_status") == "revoked",
              f"status={mem.get('lifecycle_status')}")
        # revoke 后 recall 不返回它
        r = recall(TOKEN_S1, target.get("subject", ""), "investment-research")
        revoked_ids = [i["memory_id"] for i in r.get("items", [])]
        check("revoke 后不可召回", target["memory_id"] not in revoked_ids)
        # history 保留
        r = call(TOKEN_S1, "get_memory",
                 {"memory_id": target["memory_id"], "include_history": True})
        history = r.get("history", [])
        check("revoke 后 history 保留", len(history) >= 1)
    else:
        check("有 active 记忆可 revoke", False)

    print("\n=== 8. replacement 替换 ===")
    # 先确保有一条偏好记忆
    r = capture(TOKEN_S1, "e2e-8a", "e2e-8", "e2e-8a",
                "我们部门周报格式统一用 markdown")
    # 用"不再...改为..."触发 replacement
    r = capture(TOKEN_S1, "e2e-8b", "e2e-8", "e2e-8b",
                "我们部门周报不再用 markdown 了，以后改成 LaTeX 格式")
    mems = list_memories(TOKEN_S1)
    # 检查是否有 superseded 历史
    found_history = False
    for m in mems:
        if m.get("lifecycle_status") == "active":
            try:
                r = call(TOKEN_S1, "get_memory",
                         {"memory_id": m["memory_id"], "include_history": True})
                history = r.get("history", [])
                if len(history) >= 2:
                    found_history = True
                    check("replacement 产生多 revision", len(history) >= 2,
                          f"revisions={len(history)}")
                    statuses = [h.get("lifecycle_status") for h in history]
                    check("旧版本 superseded", "superseded" in statuses)
                    check("新版本 active", "active" in statuses)
                    break
            except RuntimeError:
                continue
    if not found_history:
        check("replacement 生效", False, "未找到多 revision 历史（模型可能当作新记忆）")

    print("\n=== 9. link/revoke 关系 ===")
    # link_memories 需要 Profile 关系策略支持的类型组合
    # investment-research 的 supports: evidence_claim → thesis
    # 先确保有一条 evidence_claim 和一条 thesis
    r = capture(TOKEN_S1, "e2e-9-evidence", "e2e-9", "e2e-9-e",
                "某公司年报显示收入同比增长20%")
    r = capture(TOKEN_S1, "e2e-9-thesis", "e2e-9", "e2e-9-t",
                "我认为某公司基本面在持续改善，值得长期关注")
    mems = list_memories(TOKEN_S1)
    evidence = [m for m in mems if m.get("memory_type") == "evidence_claim" and m.get("lifecycle_status") == "active"]
    thesis = [m for m in mems if m.get("memory_type") == "thesis" and m.get("lifecycle_status") == "active"]
    if evidence and thesis:
        try:
            r = call(TOKEN_S1, "link_memories", {
                "source_memory_id": evidence[0]["memory_id"],
                "target_memory_id": thesis[0]["memory_id"],
                "relation_type": "supports",
            })
            # 检查返回结构
            relation = r.get("relation", r)
            rel_id = relation.get("relation_id")
            if not rel_id:
                # 可能嵌套在别处
                print(f"    link_memories 返回: {json.dumps(r, ensure_ascii=False)[:200]}")
            check("link_memories 成功", rel_id is not None)
            if rel_id:
                r = call(TOKEN_S1, "revoke_memory_relation", {"relation_id": rel_id})
                check("revoke_memory_relation 成功",
                      r.get("status") == "revoked" or r.get("relation", {}).get("status") == "revoked")
        except RuntimeError as e:
            check("link_memories", False, str(e))
    else:
        check("有 evidence_claim + thesis 可建关系", False,
              f"evidence={len(evidence)} thesis={len(thesis)}")

    print("\n=== 10. 团队记忆 revoke ===")
    mems = list_memories(TOKEN_S1)
    team_mems = [m for m in mems if m.get("owner_id") == "tenant-001:team:research-dept"
                 and m.get("lifecycle_status") == "active"]
    if team_mems:
        target = team_mems[0]
        r = call(TOKEN_S1, "revoke_memory", {"memory_id": target["memory_id"]})
        mem = r.get("memory", {})
        check("团队成员 revoke 团队记忆", mem.get("lifecycle_status") == "revoked")
        # 非成员不可 revoke（返回 not found 或 memory=null）
        r = call(TOKEN_S2, "revoke_memory", {"memory_id": target["memory_id"]})
        mem = r.get("memory", {})
        check("非成员 revoke 不可操作", mem == {} or mem.get("memory_id") is None)
    else:
        check("有团队记忆可 revoke", False, "无 active 团队记忆")

    print(f"\n{'='*50}")
    print(f"结果: {_passed} 通过, {_failed} 失败")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    run()
