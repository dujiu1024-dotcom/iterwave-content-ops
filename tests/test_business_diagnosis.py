from src.core.business_diagnosis import collect_business_context


def test_business_diagnosis_fingerprint_changes(conn, cfg):
    before = collect_business_context(conn, cfg)
    conn.execute(
        "INSERT INTO content_assets(asset_type,title,body,source_url,created_at,updated_at,version,is_active) "
        "VALUES('voice','观点','内容',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,1)"
    )
    conn.commit()
    after = collect_business_context(conn, cfg)
    assert before['fingerprint'] != after['fingerprint']


def test_business_diagnosis_collects_t4_business_profile_and_catalog(conn, cfg):
    import json

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS business_profiles(
            id INTEGER PRIMARY KEY,business_name TEXT,introduction TEXT,industry TEXT,
            business_model TEXT,business_stage TEXT,operating_goal TEXT,
            field_evidence_json TEXT,version_no INTEGER,updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS business_catalog_items(
            id TEXT PRIMARY KEY,entity_type TEXT,name TEXT,details_json TEXT,
            field_evidence_json TEXT,is_active INTEGER DEFAULT 1,created_at TEXT,updated_at TEXT
        );
    """)
    conn.execute(
        """INSERT INTO business_profiles(
            id,business_name,introduction,industry,business_model,business_stage,
            operating_goal,field_evidence_json,version_no,updated_at
        ) VALUES(1,'示例企业','经营介绍','零售','电商','成长','提升复购',?,1,CURRENT_TIMESTAMP)""",
        (json.dumps({"business_name":{"status":"user_confirmed"}}),),
    )
    conn.execute(
        """INSERT INTO business_catalog_items(
            id,entity_type,name,details_json,field_evidence_json,created_at,updated_at
        ) VALUES('product-1','product','示例产品','{"selling_points":["可验证卖点"]}',
                 '{"selling_points":{"status":"user_confirmed"}}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    conn.execute(
        """INSERT INTO metric_facts(
            fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
            source_record_type,source_record_id,measured_at,recorded_at,collection_status,created_at
        ) VALUES('fact-business-diagnosis','legacy','draft-1','content-1','xhs','views',0,
                 'count','platform_readback','review_collection_run','run-1',
                 '2026-11-04T10:00:00+00:00','2026-11-04T10:00:00+00:00','succeeded',CURRENT_TIMESTAMP)"""
    )
    conn.execute(
        """INSERT INTO business_gaps(
            gap_id,tenant_id,gap_key,title,description,severity,status,evidence_json,created_at,updated_at
        ) VALUES('gap-1','tenant-test','content-proof','内容证据不足','缺少案例素材',
                 'high','active','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    conn.commit()

    context = collect_business_context(conn, cfg)["context"]

    assert context["business_profile"]["registered_profile"]["business_name"] == "示例企业"
    assert context["business_catalog_items"][0]["entity_type"] == "product"
    assert any(
        ref["source_type"] == "business_catalog_item" and ref["source_id"] == "product-1"
        for ref in context["source_references"]
    )
    assert context["unified_metric_facts"][0]["value"] == 0
    assert context["active_business_gaps"][0]["gap_key"] == "content-proof"
    assert any(ref["source_type"] == "metric_fact" for ref in context["source_references"])
    assert any(ref["source_type"] == "business_gap" for ref in context["source_references"])


def test_business_diagnosis_collects_t5_original_asset_inventory_and_gaps(conn, cfg):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS original_assets(
            id INTEGER PRIMARY KEY,asset_type TEXT,title TEXT,description TEXT,source_kind TEXT,
            source_media_asset_id INTEGER,source_content_asset_id INTEGER,source_kb_id TEXT,
            source_uri TEXT,source_version TEXT,current_version_no INTEGER,readability_status TEXT,
            completeness_status TEXT,duplicate_status TEXT,freshness_status TEXT,description_status TEXT,
            commercial_use_status TEXT,privacy_status TEXT,review_status TEXT,archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS original_asset_gaps(
            id INTEGER PRIMARY KEY,entity_type TEXT,entity_id TEXT,asset_type TEXT,title TEXT,
            reason TEXT,priority TEXT,status TEXT,source_plan_id TEXT,resolved_asset_id INTEGER,
            created_at TEXT,updated_at TEXT
        );
    """)
    conn.execute(
        """INSERT INTO original_assets(
            asset_type,title,description,source_kind,readability_status,completeness_status,
            duplicate_status,freshness_status,commercial_use_status,privacy_status,review_status
        ) VALUES('product_image','已确认产品图','清晰产品图','manual','readable','complete',
                 'unique','current','allowed','clear','confirmed')"""
    )
    conn.execute(
        """INSERT INTO original_asset_gaps(
            entity_type,entity_id,asset_type,title,reason,priority,status
        ) VALUES('product','product-1','product_manual','缺少产品说明书','尚未收集','high','open')"""
    )
    conn.commit()

    context = collect_business_context(conn, cfg)["context"]

    assert len(context["original_asset_inventory"]) == 1
    assert context["inventory_summary"]["original_asset_usable_count"] == 1
    assert context["inventory_summary"]["original_asset_open_gap_count"] == 1
    assert any(ref["source_type"] == "original_asset" for ref in context["source_references"])
    assert any(ref["source_type"] == "original_asset_gap" for ref in context["source_references"])


def test_business_diagnosis_builds_effect_summary_from_latest_facts_and_account_snapshots(conn, cfg):
    conn.execute(
        """INSERT INTO metric_facts(
            fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
            source_record_type,source_record_id,measured_at,recorded_at,collection_status,created_at
        ) VALUES
            ('effect-views-old','legacy','draft-effect','content-effect','xhs','views',100,
             'count','platform_readback','review_collection_run','effect-run-old',
             '2026-11-01T10:00:00+00:00','2026-11-01T10:00:00+00:00','succeeded',CURRENT_TIMESTAMP),
            ('effect-views-new','legacy','draft-effect','content-effect','xhs','views',250,
             'count','manual','metric_snapshot','effect-snapshot-new',
             '2026-11-02T10:00:00+00:00','2026-11-02T10:00:00+00:00','succeeded',CURRENT_TIMESTAMP),
            ('effect-likes-new','legacy','draft-effect','content-effect','xhs','likes',20,
             'count','platform_readback','review_collection_run','effect-run-likes',
             '2026-11-02T10:00:00+00:00','2026-11-02T10:00:00+00:00','succeeded',CURRENT_TIMESTAMP)
        """
    )
    conn.execute(
        """INSERT INTO account_snapshots(platform,followers,likes_total,captured_at,source)
           VALUES('xhs',100,300,'2026-11-01T10:00:00+00:00','manual'),
                 ('xhs',145,360,'2026-11-02T10:00:00+00:00','manual')"""
    )
    conn.commit()

    context = collect_business_context(conn, cfg)["context"]
    effect = context["effect_summary"]

    assert effect["platform_performance"]["xhs"]["content_count"] == 1
    assert effect["platform_performance"]["xhs"]["metric_totals"]["views"] == 250
    assert effect["platform_performance"]["xhs"]["metric_totals"]["likes"] == 20
    assert effect["follower_growth"]["xhs"]["latest_followers"] == 145
    assert effect["follower_growth"]["xhs"]["net_growth_in_window"] == 45
    assert effect["goal_completion"]["total"] == len(context["business_goals"])
    assert any(
        ref["source_type"] == "account_snapshot"
        for ref in context["source_references"]
    )


def test_business_diagnosis_feedback_snapshot_does_not_invalidate_source_fingerprint(conn, cfg):
    before = collect_business_context(conn, cfg)
    conn.execute(
        """INSERT INTO account_snapshots(platform,followers,likes_total,captured_at,source)
           VALUES('xhs',123,456,'2026-11-06T10:00:00+00:00','manual')"""
    )
    conn.commit()
    after = collect_business_context(conn, cfg)

    assert before["fingerprint"] == after["fingerprint"]
    assert after["context"]["effect_summary"]["follower_growth"]["xhs"]["latest_followers"] == 123
