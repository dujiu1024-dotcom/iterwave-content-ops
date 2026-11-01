from src.core.skills_registry import builtin_skills_catalog


def test_builtin_skill_catalog_has_unique_ids_and_explicit_lifecycle_states():
    skills = builtin_skills_catalog()

    assert skills
    skill_ids = [skill["skill_id"] for skill in skills]
    assert len(skill_ids) == len(set(skill_ids))

    for skill in skills:
        assert skill["installation_state"] == "built_in"
        assert skill["enabled_state"] == "system_managed"
        assert skill["runnable_state"] == "workflow_managed"
        assert skill["execution_mode"] == "trusted_application_workflow"
        assert skill["capability_status"] in {"normal", "limited"}
        assert skill["description"]


def test_builtin_skill_catalog_returns_independent_metadata():
    first = builtin_skills_catalog()
    first[0]["name"] = "mutated"

    second = builtin_skills_catalog()
    assert second[0]["name"] != "mutated"
