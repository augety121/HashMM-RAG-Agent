from pathlib import Path

from hashmm.agent.role_catalog import agency_roles, load_role_prompt, shortlist_roles


def test_complete_agency_catalog_is_vendored_and_unique():
    roles = agency_roles()
    assert len(roles) == 271
    assert len({role["id"] for role in roles}) == 271
    assert len({role["category"] for role in roles}) == 18
    assert all(role["source"] == "agency-agents" for role in roles)
    assert all(role["license"] == "MIT" for role in roles)


def test_role_prompt_is_lazy_and_confined_to_catalog():
    role = agency_roles()[0]
    prompt = load_role_prompt(role["id"])
    assert prompt
    assert not prompt.startswith("---")
    assert load_role_prompt("agency.../../../outside") == ""
    assert len(prompt) <= 24_000


def test_auto_shortlist_is_bounded_but_manual_catalog_is_complete():
    matches = shortlist_roles("security application audit", limit=12)
    assert 0 < len(matches) <= 12
    assert all(role in agency_roles() for role in matches)


def test_vendor_license_and_index_are_present():
    root = Path(__file__).parents[1] / "hashmm" / "agent" / "role_catalog" / "agency-agents"
    assert (root / "LICENSE.upstream").is_file()
    assert (root / "index.json").is_file()
