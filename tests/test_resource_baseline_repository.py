import sqlite3
from src.core.resource_baseline_repository import build_resource_baseline

def test_unreviewed_or_unlicensed_assets_are_not_reported_as_available():
 c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row
 c.executescript("""
 CREATE TABLE original_assets(id INTEGER PRIMARY KEY,asset_type TEXT,title TEXT,description TEXT,source_kind TEXT,source_media_asset_id INTEGER,source_content_asset_id INTEGER,source_kb_id TEXT,source_uri TEXT,source_version TEXT,review_status TEXT,commercial_use_status TEXT,readability_status TEXT,completeness_status TEXT,duplicate_status TEXT,freshness_status TEXT,privacy_status TEXT,description_status TEXT,updated_at TEXT,archived_at TEXT);
 CREATE TABLE original_asset_links(id INTEGER PRIMARY KEY,original_asset_id INTEGER,entity_type TEXT,entity_id TEXT,relation_type TEXT);
 INSERT INTO original_assets VALUES(1,'product_image','待审核图片','','external',NULL,NULL,NULL,'https://example.test/a.png','v1','pending','unknown','unknown','unknown','unknown','unknown','unknown','present','now',NULL);
 INSERT INTO original_assets VALUES(2,'customer_case','已确认但许可未知','','kb_source',NULL,NULL,'kb-1',NULL,'v1','confirmed','unknown','readable','complete','unique','current','clear','present','now',NULL);
 INSERT INTO original_assets VALUES(3,'brand_material','许可禁止','','manual',NULL,NULL,NULL,NULL,'v1','confirmed','prohibited','readable','complete','unique','current','clear','present','now',NULL);
 INSERT INTO original_assets VALUES(4,'product_description','允许使用','','external',NULL,NULL,NULL,'https://example.test/b','v2','confirmed','allowed','readable','complete','unique','current','clear','present','now',NULL);
 INSERT INTO original_asset_links VALUES(1,4,'product','p1','supports');
 """)
 result=build_resource_baseline(c)
 statuses={item["source_id"]:item["status"] for item in result["resources"] if item["source_type"] in {"external","kb_source","manual"}}
 assert statuses=={"1":"pending","2":"pending","3":"rejected","4":"confirmed"}
 c.execute("UPDATE original_assets SET privacy_status='contains_personal_data' WHERE id=4")
 result=build_resource_baseline(c)
 restricted=next(item for item in result["resources"] if item["source_type"]=="external" and item["source_id"]=="4")
 assert restricted["status"]=="restricted"
 c.close()
