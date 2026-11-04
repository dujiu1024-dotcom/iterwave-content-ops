"""Versioned account-group North Star persistence."""
from __future__ import annotations
import json
from datetime import datetime, timezone

FIELDS = {
 "positioning": 2000, "business_direction": 4000, "target_users": 4000,
 "core_value": 4000, "long_term_content_direction": 4000, "business_model": 2000,
 "platform_relationships": 4000, "long_term_goals": 4000,
}
def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def _decode(row):
 if row is None: return None
 result=dict(row)
 try: result["snapshot"]=json.loads(result.pop("snapshot_json"))
 except (TypeError,ValueError): result["snapshot"]={}
 return result
def get_current(conn):
 return _decode(conn.execute("SELECT * FROM account_group_north_star_versions WHERE status='effective' ORDER BY version_no DESC LIMIT 1").fetchone())
def get_version(conn, version_id):
 return _decode(conn.execute("SELECT * FROM account_group_north_star_versions WHERE id=?",(version_id,)).fetchone())
def list_versions(conn, limit=50):
 rows=conn.execute("SELECT id,version_no,status,change_reason,created_by,created_at,effective_at,superseded_at FROM account_group_north_star_versions ORDER BY version_no DESC LIMIT ?",(max(1,min(int(limit),200)),)).fetchall()
 return [dict(row) for row in rows]
def create_version(conn,payload,*,actor="user"):
 if not isinstance(payload,dict): raise ValueError("北极星内容必须是对象")
 reason=str(payload.get("change_reason") or "").strip()
 if not reason: raise ValueError("修改原因不能为空")
 supplied=payload.get("snapshot") if "snapshot" in payload else {key:value for key,value in payload.items() if key!="change_reason"}
 if not isinstance(supplied,dict): raise ValueError("北极星快照必须是对象")
 unknown=set(supplied)-set(FIELDS)
 if unknown: raise ValueError("北极星包含不支持的字段："+ "、".join(sorted(unknown)))
 snapshot={}
 for field,maximum in FIELDS.items():
  value=supplied.get(field,"")
  if field=="platform_relationships" and isinstance(value,list): value="、".join(str(x).strip() for x in value if str(x).strip())
  if not isinstance(value,str): raise ValueError(field+" 必须是文本")
  value=value.strip()
  if len(value)>maximum: raise ValueError(field+" 内容过长")
  snapshot[field]=value
 required=("positioning","business_direction","target_users","core_value","long_term_content_direction","business_model","long_term_goals")
 missing=[field for field in required if not snapshot[field]]
 if missing: raise ValueError("请补齐北极星必填项："+ "、".join(missing))
 now=_now(); own_tx=not conn.in_transaction; savepoint="north_star_write"
 if own_tx: conn.execute("BEGIN IMMEDIATE")
 else: conn.execute("SAVEPOINT "+savepoint)
 try:
  version=int(conn.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM account_group_north_star_versions").fetchone()[0])
  conn.execute("UPDATE account_group_north_star_versions SET status='archived',superseded_at=? WHERE status='effective'",(now,))
  cur=conn.execute("INSERT INTO account_group_north_star_versions(version_no,snapshot_json,status,change_reason,created_by,created_at,effective_at) VALUES(?,?,'effective',?,?,?,?)",(version,json.dumps(snapshot,ensure_ascii=False,sort_keys=True),reason[:1000],actor[:100],now,now))
  result=get_version(conn,int(cur.lastrowid))
  if own_tx: conn.commit()
  else: conn.execute("RELEASE SAVEPOINT "+savepoint)
  return result
 except Exception:
  if own_tx: conn.rollback()
  else:
   conn.execute("ROLLBACK TO SAVEPOINT "+savepoint); conn.execute("RELEASE SAVEPOINT "+savepoint)
  raise
