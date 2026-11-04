from fastapi import FastAPI
from fastapi.testclient import TestClient
import sqlite3
from src.api.business_profile import register_business_profile_routes

def test_service_only_business_does_not_require_physical_product():
 c=sqlite3.connect(":memory:",check_same_thread=False); c.row_factory=sqlite3.Row
 c.executescript("""
 CREATE TABLE business_profiles(id INTEGER PRIMARY KEY,business_name TEXT,introduction TEXT,industry TEXT,business_model TEXT,business_stage TEXT,operating_goal TEXT,field_evidence_json TEXT,version_no INTEGER,updated_at TEXT);
 INSERT INTO business_profiles VALUES(1,'顾问工作室','提供专业咨询','咨询','服务型业务','起步期','建立长期客户关系','{}',1,'now');
 CREATE TABLE business_catalog_items(id TEXT PRIMARY KEY,entity_type TEXT,name TEXT,details_json TEXT,field_evidence_json TEXT,is_active INTEGER,created_at TEXT,updated_at TEXT);
 """)
 app=FastAPI(); register_business_profile_routes(app,lambda:{"conn":c,"cfg":None})
 result=TestClient(app).get("/api/business-profile/analysis")
 assert result.status_code==200
 missing=[item["field"] for item in result.json()["missing"]]
 assert "offerings" not in missing
 assert "audience" in missing
 c.close()
