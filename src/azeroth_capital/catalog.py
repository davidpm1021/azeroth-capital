from __future__ import annotations

from dataclasses import dataclass

from .blizzard import BlizzardClient
from .storage import Storage


@dataclass(frozen=True)
class CatalogResult:
    expansion: str
    professions: int
    skill_tiers: int
    recipes: int
    items: int


def _recipe_ids(node: object) -> set[int]:
    found: set[int] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "recipes" and isinstance(value, list):
                for recipe in value:
                    if isinstance(recipe, dict) and recipe.get("id") is not None:
                        found.add(int(recipe["id"]))
            else:
                found.update(_recipe_ids(value))
    elif isinstance(node, list):
        for value in node:
            found.update(_recipe_ids(value))
    return found


def _item_ids_from_recipe(payload: dict) -> dict[int, set[str]]:
    items: dict[int, set[str]] = {}

    def add(item_id: int, role: str) -> None:
        items.setdefault(int(item_id), set()).add(role)

    for key in ("crafted_item", "alliance_crafted_item", "horde_crafted_item"):
        value = payload.get(key)
        if isinstance(value, dict) and value.get("id") is not None:
            add(int(value["id"]), "crafted")

    for reagent_entry in payload.get("reagents", []) or []:
        if not isinstance(reagent_entry, dict):
            continue
        reagent = reagent_entry.get("reagent")
        if isinstance(reagent, dict) and reagent.get("id") is not None:
            add(int(reagent["id"]), "reagent")

    return items


def sync_expansion_catalog(
    client: BlizzardClient,
    storage: Storage,
    expansion: str = "Midnight",
) -> CatalogResult:
    profession_index = client.profession_index()
    profession_count = 0
    tier_count = 0
    all_recipe_ids: set[int] = set()
    item_roles: dict[int, set[str]] = {}

    for profession_ref in profession_index.get("professions", []):
        profession_id = profession_ref.get("id")
        if profession_id is None:
            continue
        profession = client.profession(int(profession_id))
        matching_tiers = [
            tier
            for tier in profession.get("skill_tiers", [])
            if expansion.casefold() in str(tier.get("name", "")).casefold()
        ]
        if not matching_tiers:
            continue

        profession_count += 1
        for tier in matching_tiers:
            tier_id = tier.get("id")
            if tier_id is None:
                continue
            tier_count += 1
            tier_payload = client.profession_skill_tier(int(profession_id), int(tier_id))
            all_recipe_ids.update(_recipe_ids(tier_payload))

    for recipe_id in sorted(all_recipe_ids):
        payload = client.recipe(recipe_id)
        for item_id, roles in _item_ids_from_recipe(payload).items():
            item_roles.setdefault(item_id, set()).update(roles)

    storage.replace_expansion_catalog(expansion, item_roles)

    return CatalogResult(
        expansion=expansion,
        professions=profession_count,
        skill_tiers=tier_count,
        recipes=len(all_recipe_ids),
        items=len(item_roles),
    )
