"""Build and freeze a traceable resource baseline using existing source systems."""
from __future__ import annotations
import json
from datetime import datetime,timezone

def _tables(conn): return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
def build_resource_baseline(conn):
 tables=_tables(conn); resources=[]; gaps=[]; profile={}
 if "business_profiles" in tables:
  from src.core.business_profile_repository import get_snapshot
  profile=get_snapshot(conn); p=profile["profile"]
  evidence=p.get("field_evidence") or {}
  profile_fields=("business_name","introduction","industry","business_model","operating_goal")
  profile_complete=all(str(p.get(field) or "").strip() for field in profile_fields)
  profile_confirmed=profile_complete and all(evidence.get(field,{}).get("status") in ("user_confirmed","external_source") for field in profile_fields)
  profile_status="missing" if not int(p.get("version_no") or 0) else ("confirmed" if profile_confirmed else "pending")
  resources.append({"category":"business_profile","title":"业务资料档案","source_type":"business_profile","source_id":"current","status":profile_status,"updated_at":p.get("updated_at"),"details":{"profile_version":p.get("version_no"),"catalog_count":len(profile["items"])}})
  if not p.get("version_no"): gaps.append({"category":"business_profile","title":"尚未建立业务资料档案","status":"open"})
  for item in profile["items"]:
   item_evidence=item.get("field_evidence") or {}
   item_details=item.get("details") or {}
   # Every declared detail field must have its own trusted evidence. Empty or
   # partial evidence must never make a resource appear confirmed.
   item_confirmed=bool(item_details) and all(
       field in item_evidence
       and isinstance(item_evidence[field],dict)
       and item_evidence[field].get("status") in ("user_confirmed","external_source")
       for field in item_details
   )
   resources.append({"category":item["entity_type"],"title":item["name"],"source_type":"business_catalog_items","source_id":item["id"],"status":"confirmed" if item_confirmed else "pending","updated_at":item.get("updated_at"),"details":item.get("details") or {}})
 if "original_assets" in tables:
  rows=conn.execute("SELECT id,asset_type,title,description,source_kind,source_media_asset_id,source_content_asset_id,source_kb_id,source_uri,source_version,review_status,commercial_use_status,readability_status,completeness_status,duplicate_status,freshness_status,privacy_status,description_status,updated_at,archived_at FROM original_assets ORDER BY updated_at DESC,id DESC").fetchall()
  for row in rows:
   item=dict(row)
   if item.get("archived_at"): continue
   if item.get("review_status")=="rejected" or item.get("commercial_use_status")=="prohibited":
    status="rejected"
   elif item.get("commercial_use_status")=="restricted" or item.get("privacy_status") in ("contains_personal_data","restricted"):
    status="restricted"
   elif (item.get("review_status")=="confirmed"
         and item.get("commercial_use_status")=="allowed"
         and item.get("readability_status") in ("readable","not_applicable")
         and item.get("completeness_status")=="complete"
         and item.get("duplicate_status")=="unique"
         and item.get("freshness_status")=="current"
         and item.get("privacy_status") in ("clear","not_applicable")):
    status="confirmed"
   else:
    status="pending"
   linked=[]
   if "original_asset_links" in tables:
    linked=[dict(x) for x in conn.execute("SELECT entity_type,entity_id,relation_type FROM original_asset_links WHERE original_asset_id=? ORDER BY id",(item["id"],)).fetchall()]
   category = {"kb_source":"internal_reference","external":"external_reference","media_asset":"original_image","content_asset":"historical_content"}.get(item["source_kind"],"original_asset")
   resources.append({"category":category,"title":item["title"],"source_type":item["source_kind"],"source_id":str(item["id"]),"source_uri":item.get("source_uri"),"source_version":item.get("source_version"),"status":status,"updated_at":item.get("updated_at"),"details":{**{k:item.get(k) for k in ("asset_type","description","commercial_use_status","readability_status","completeness_status","duplicate_status","freshness_status","privacy_status","description_status","source_media_asset_id","source_content_asset_id","source_kb_id")},"linked_entities":linked}})
   if status!="confirmed": gaps.append({"category":"original_asset","title":item["title"],"status":"review_required","source_id":str(item["id"])})
 if "original_asset_gaps" in tables:
  for row in conn.execute("SELECT id,entity_type,entity_id,asset_type,title,reason,priority,status,updated_at FROM original_asset_gaps WHERE status IN ('open','in_progress') ORDER BY id").fetchall(): gaps.append({"category":"original_asset_gap",**dict(row)})
 if "spiral_rounds" in tables:
  count=int(conn.execute("SELECT COUNT(*) FROM spiral_rounds").fetchone()[0])
  content_count=int(conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0]) if "contents" in tables else 0
  draft_count=int(conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]) if "drafts" in tables else 0
  resources.append({"category":"historical_output","title":"历史内容生产与产出记录","source_type":"spiral_rounds","source_id":"aggregate","status":"confirmed" if count or content_count or draft_count else "missing","updated_at":None,"details":{"round_count":count,"content_count":content_count,"draft_count":draft_count}})
  if not count and not content_count and not draft_count: gaps.append({"category":"historical_output","title":"暂无历史内容生产记录","status":"open"})
 if "platform_insights" in tables:
  metrics=int(conn.execute("SELECT COUNT(*) FROM platform_insights").fetchone()[0])
  resources.append({"category":"historical_performance","title":"平台历史表现数据","source_type":"platform_insights","source_id":"aggregate","status":"confirmed" if metrics else "missing","updated_at":None,"details":{"metric_rows":metrics}})
  if not metrics: gaps.append({"category":"historical_performance","title":"暂无平台历史表现数据","status":"open"})
 else:
  resources.append({"category":"historical_performance","title":"平台历史表现数据","source_type":"platform_insights","source_id":"not_available","status":"unknown","updated_at":None,"details":{"note":"当前数据库未发现表现数据来源表"}})
 for table,category,title in (("content_templates","template_tool","内容模板库"),("tenant_skills","template_tool","账号组工具与技能"),("production_lines","production_capacity","生产线能力")):
  if table not in tables:
   resources.append({"category":category,"title":title,"source_type":table,"source_id":"not_available","status":"unknown","updated_at":None,"details":{"note":"未发现来源表，未推断为已具备"}}); continue
  count=int(conn.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0]); resources.append({"category":category,"title":title,"source_type":table,"source_id":"aggregate","status":"pending" if count else "missing","updated_at":None,"details":{"count":count,"note":"仅检测到来源记录；尚无独立能力确认状态"}})
  if not count: gaps.append({"category":category,"title":title+"尚未配置","status":"open"})
 return {"captured_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"business_profile":profile,"resources":resources,"gaps":gaps,"summary":{"total":len(resources),"confirmed":sum(x["status"]=="confirmed" for x in resources),"pending":sum(x["status"]=="pending" for x in resources),"restricted":sum(x["status"]=="restricted" for x in resources),"rejected":sum(x["status"]=="rejected" for x in resources),"missing":sum(x["status"]=="missing" for x in resources),"unknown":sum(x["status"]=="unknown" for x in resources),"gaps":len(gaps)}}
def capture_resource_baseline(conn,master_plan_version_id,*,actor="system"):
 snapshot=build_resource_baseline(conn)
 conn.execute("INSERT OR IGNORE INTO master_plan_resource_baselines(master_plan_version_id,snapshot_json,captured_at,captured_by) VALUES(?,?,?,?)",(master_plan_version_id,json.dumps(snapshot,ensure_ascii=False,sort_keys=True),snapshot["captured_at"],actor[:100]))
 return snapshot
def get_plan_resource_baseline(conn,master_plan_version_id):
 row=conn.execute("SELECT snapshot_json,captured_at,captured_by FROM master_plan_resource_baselines WHERE master_plan_version_id=?",(master_plan_version_id,)).fetchone()
 if row is None: return None
 try: snapshot=json.loads(row["snapshot_json"])
 except (TypeError,ValueError): return None
 return {"snapshot":snapshot,"captured_at":row["captured_at"],"captured_by":row["captured_by"]}
