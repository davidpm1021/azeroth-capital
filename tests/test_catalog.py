from pathlib import Path

from azeroth_capital.catalog import sync_expansion_catalog
from azeroth_capital.storage import Storage


class FakeCatalogClient:
    def profession_index(self):
        return {
            "professions": [
                {"id": 171, "name": "Alchemy"},
                {"id": 164, "name": "Blacksmithing"},
            ]
        }

    def profession(self, profession_id: int):
        if profession_id == 171:
            return {
                "skill_tiers": [
                    {"id": 1, "name": "Midnight Alchemy"},
                    {"id": 2, "name": "Dragon Isles Alchemy"},
                ]
            }
        return {"skill_tiers": [{"id": 3, "name": "Midnight Blacksmithing"}]}

    def profession_skill_tier(self, profession_id: int, skill_tier_id: int):
        if skill_tier_id == 1:
            return {
                "categories": [
                    {"name": "Reagents", "recipes": [{"id": 100}, {"id": 101}]}
                ]
            }
        if skill_tier_id == 3:
            return {
                "categories": [
                    {"name": "Smelting", "recipes": [{"id": 200}]}
                ]
            }
        raise AssertionError("legacy tier should not be fetched")

    def recipe(self, recipe_id: int):
        if recipe_id == 100:
            return {
                "crafted_item": {"id": 500},
                "reagents": [
                    {"reagent": {"id": 600}, "quantity": 4},
                    {"reagent": {"id": 601}, "quantity": 2},
                ],
            }
        if recipe_id == 101:
            return {
                "crafted_item": {"id": 501},
                "reagents": [{"reagent": {"id": 500}, "quantity": 1}],
            }
        if recipe_id == 200:
            return {
                "crafted_item": {"id": 700},
                "reagents": [{"reagent": {"id": 701}, "quantity": 5}],
            }
        raise AssertionError(recipe_id)


def test_sync_expansion_catalog_builds_recipe_item_universe(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()

    result = sync_expansion_catalog(FakeCatalogClient(), storage, "Midnight")

    assert result.professions == 2
    assert result.skill_tiers == 2
    assert result.recipes == 3
    assert result.items == 6
    assert storage.expansion_item_ids("Midnight") == {500, 501, 600, 601, 700, 701}
    status = storage.expansion_catalog_status("Midnight")
    assert status["items"] == 6
    assert status["updated_at"] is not None
