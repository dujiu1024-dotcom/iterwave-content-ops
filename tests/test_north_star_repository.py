import sqlite3
import pytest
from src.core.north_star_repository import create_version,get_current,get_version,list_versions

def db():
 c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row
 c.executescript("""CREATE TABLE account_group_north_star_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,version_no INTEGER UNIQUE,snapshot_json TEXT,status TEXT,change_reason TEXT,created_by TEXT,created_at TEXT,effective_at TEXT,superseded_at TEXT); CREATE UNIQUE INDEX one_effective ON account_group_north_star_versions(status) WHERE status='effective';""")
 return c

def payload(**changes):
 x={"positioning":"专业知识型个人IP","business_direction":"围绕专业服务建立信任","target_users":"有明确需求的目标客户","core_value":"提供可验证的专业解决方案","long_term_content_direction":"案例、方法论和实操经验","business_model":"服务型业务","platform_relationships":"小红书获客，公众号沉淀","long_term_goals":"建立稳定的专业服务品牌","change_reason":"首次建立账号组北极星"}; x.update(changes); return x

def test_versions_preserve_history_and_single_effective():
 c=db(); a=create_version(c,payload(),actor="tester"); b=create_version(c,payload(positioning="升级后的专业服务品牌",change_reason="定位升级"))
 assert a["status"]=="effective" and b["status"]=="effective"
 assert get_version(c,a["id"])["status"]=="archived" and get_current(c)["id"]==b["id"]
 assert [x["version_no"] for x in list_versions(c)]==[2,1]
 assert get_version(c,a["id"])["snapshot"]["positioning"]=="专业知识型个人IP"

def test_reason_and_required_fields():
 c=db()
 with pytest.raises(ValueError,match="修改原因"): create_version(c,payload(change_reason=""))
 with pytest.raises(ValueError,match="必填项"): create_version(c,payload(target_users=""))

def test_unknown_fields_rejected():
 with pytest.raises(ValueError,match="不支持"): create_version(db(),payload(unexpected="x"))
