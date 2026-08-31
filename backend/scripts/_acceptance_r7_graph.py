"""阶段七 7.6 图谱隔离验收（Neo4j 数据层）。

HTTP /graph/export 需 DASHSCOPE_API_KEY（当前 .env 为占位符），故在数据层直接验证：
1) 各工作区实体/关系计数互不重叠、可按 workspace_id 精确切分；
2) 跨工作区同名实体可共存（验证 (name, workspace_id) 复合约束生效——此前旧 name 约束会冲突）；
3) 同工作区同名幂等合并为 1。
"""
from __future__ import annotations

from neo4j import GraphDatabase
from app.core.config import settings

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} — {detail}")


def run() -> None:
    drv = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=6,
    )
    db = settings.neo4j_database
    with drv.session(database=db) as s:

        def count(q: str, **p):
            return s.run(q, p).single()[0]

        print("=== 7.6 图谱隔离：工作区计数与切分 ===")
        w1 = count("MATCH (e:Entity) WHERE e.workspace_id=1 RETURN count(e)")
        w2 = count("MATCH (e:Entity) WHERE e.workspace_id=2 RETURN count(e)")
        w3 = count("MATCH (e:Entity) WHERE e.workspace_id=3 RETURN count(e)")
        tot = count("MATCH (e:Entity) RETURN count(e)")
        check("实体按工作区分摊无遗漏（1+2+3=total）", w1 + w2 + w3 == tot, f"{w1}+{w2}+{w3} vs {tot}")
        check("所有实体都带 workspace_id（无 NULL）",
              count("MATCH (e:Entity) WHERE e.workspace_id IS NULL RETURN count(e)") == 0)
        # ws3 为历史数据默认空间，应远大于新测试账号
        check("历史工作区(3)实体占主体", w3 > w1 and w3 > w2, f"w1={w1} w2={w2} w3={w3}")
        r3 = count("MATCH ()-[r:RELATES_TO]->() WHERE r.workspace_id=3 RETURN count(r)")
        r_all = count("MATCH ()-[r:RELATES_TO]->() RETURN count(r)")
        check("关系边带 workspace_id 且按区归属",
              count("MATCH ()-[r:RELATES_TO]->() WHERE r.workspace_id IS NULL RETURN count(r)") == 0
              and r3 > 0 and r3 <= r_all, f"r3={r3}/{r_all}")

        print("=== 7.6 复合约束：跨区同名共存 + 同区幂等 ===")
        # 同区两次 MERGE → 仍 1 个
        for ws in (1, 2):
            s.run("MERGE (e:Entity {name:$n, workspace_id:$ws}) "
                  "ON CREATE SET e.entity_type='验收占位', e.description=$n",
                  n="_iso_dup_check", ws=ws)
            s.run("MERGE (e:Entity {name:$n, workspace_id:$ws}) "
                  "ON CREATE SET e.entity_type='验收占位', e.description=$n",
                  n="_iso_dup_check", ws=ws)
        c1 = count("MATCH (e:Entity {name:'_iso_dup_check', workspace_id:1}) RETURN count(e)")
        c2 = count("MATCH (e:Entity {name:'_iso_dup_check', workspace_id:2}) RETURN count(e)")
        check("同工作区同名幂等为 1", c1 == 1, str(c1))
        check("跨工作区同名实体各自独立共存", c1 == 1 and c2 == 1, f"c1={c1} c2={c2}")

        # 清理占位，并验证已能按 (name, workspace_id) 精确查询
        s.run("MATCH (e:Entity {name:'_iso_dup_check'}) DETACH DELETE e")
        left = count("MATCH (e:Entity {name:'_iso_dup_check'}) RETURN count(e)")
        check("占位清理完成", left == 0, str(left))

    drv.close()
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    run()