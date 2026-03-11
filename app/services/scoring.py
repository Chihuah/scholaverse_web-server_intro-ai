"""Scoring rules engine — converts learning scores to RPG attribute options.

Implements the score-to-attribute conversion rules from system-spec.md section 4.
"""

from __future__ import annotations

import json
import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Score tier boundaries (inclusive)
# ---------------------------------------------------------------------------
TIER_S = (90, 100)  # Best
TIER_A = (75, 89)
TIER_B = (60, 74)
TIER_C = (40, 59)
TIER_D = (0, 39)    # Worst


def _tier(score: float) -> str:
    """Return tier label for a numeric score."""
    if score >= 90:
        return "S"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


# ---------------------------------------------------------------------------
# Unit 1 — Race & Gender
# ---------------------------------------------------------------------------
RACE_OPTIONS: dict[str, list[str]] = {
    "S": ["elf", "human", "orc", "dwarf", "dragon"],
    "A": ["elf", "human", "orc", "dwarf"],
    "B": ["human", "orc", "dwarf"],
    "C": ["dwarf", "pixie"],
    "D": ["plant", "slime"],
}

RACE_LABELS: dict[str, str] = {
    "elf": "精靈",
    "human": "人類",
    "orc": "獸人",
    "dwarf": "矮人",
    "dragon": "龍族",
    "pixie": "小精靈",
    "plant": "植物",
    "slime": "史萊姆",
}

GENDER_OPTIONS: list[str] = ["male", "female", "neutral"]
GENDER_LABELS: dict[str, str] = {
    "male": "男性",
    "female": "女性",
    "neutral": "中性",
}

# ---------------------------------------------------------------------------
# Unit 2 — Class & Body
# ---------------------------------------------------------------------------
CLASS_OPTIONS: dict[str, list[str]] = {
    "S": ["archmage", "paladin", "ranger", "assassin", "priest"],
    "A": ["mage", "warrior", "archer", "priest"],
    "B": ["warrior", "archer", "priest"],
    "C": ["militia", "apprentice"],
    "D": ["farmer"],
}

CLASS_LABELS: dict[str, str] = {
    "archmage": "大法師",
    "paladin": "聖騎士",
    "ranger": "遊俠",
    "assassin": "刺客",
    "priest": "牧師",
    "mage": "法師",
    "warrior": "戰士",
    "archer": "弓箭手",
    "militia": "民兵",
    "apprentice": "學徒",
    "farmer": "農夫",
}

BODY_OPTIONS: dict[str, list[str]] = {
    "S": ["muscular", "standard", "slim"],
    "A": ["muscular", "standard", "slim"],
    "B": ["standard", "slim"],
    "C": ["standard", "slim"],
    "D": ["slim"],
}

BODY_LABELS: dict[str, str] = {
    "muscular": "結實精壯",
    "standard": "標準",
    "slim": "纖細瘦弱",
}

# ---------------------------------------------------------------------------
# Unit 3 — Equipment
# ---------------------------------------------------------------------------
EQUIPMENT_OPTIONS: dict[str, list[str]] = {
    "S": ["legendary"],
    "A": ["fine"],
    "B": ["common"],
    "C": ["crude"],
    "D": ["broken"],
}

EQUIPMENT_LABELS: dict[str, str] = {
    "legendary": "傳說級（華麗精緻）",
    "fine": "精良級（完好精美）",
    "crude": "粗糙級（簡陋）",
    "common": "普通級（一般完好）",
    "broken": "破損級（破爛）",
}

# ---------------------------------------------------------------------------
# Unit 4 — Weapon (quality + types)
# ---------------------------------------------------------------------------
WEAPON_QUALITY: dict[str, str] = {
    "S": "artifact",
    "A": "fine",
    "B": "common",
    "C": "crude",
    "D": "primitive",
}

WEAPON_QUALITY_LABELS: dict[str, str] = {
    "artifact": "神器級",
    "fine": "精良級",
    "common": "普通級",
    "crude": "粗糙級",
    "primitive": "原始",
}

# Weapon types available per tier (class-independent base list)
WEAPON_TYPES_BY_TIER: dict[str, list[str]] = {
    "S": ["sword", "shield", "staff", "spellbook", "bow", "dagger", "mace", "spear"],
    "A": ["sword", "shield", "staff", "bow", "dagger", "mace"],
    "B": ["sword", "staff", "bow", "dagger"],
    "C": ["short_sword", "club"],
    "D": ["wooden_stick", "stone"],
}

# Class-specific weapon affinities — if a class is specified, filter to these
CLASS_WEAPON_AFFINITY: dict[str, list[str]] = {
    "archmage": ["staff", "spellbook"],
    "mage": ["staff", "spellbook"],
    "paladin": ["sword", "shield", "mace"],
    "warrior": ["sword", "shield", "mace", "spear"],
    "ranger": ["bow", "dagger", "sword"],
    "archer": ["bow", "dagger"],
    "assassin": ["dagger", "short_sword"],
    "priest": ["staff", "mace"],
    "militia": ["short_sword", "club", "spear"],
    "apprentice": ["staff", "short_sword"],
    "farmer": ["wooden_stick", "stone"],
}

WEAPON_TYPE_LABELS: dict[str, str] = {
    "sword": "長劍",
    "shield": "盾牌",
    "staff": "法杖",
    "spellbook": "魔法書",
    "bow": "弓",
    "dagger": "匕首",
    "mace": "錘",
    "spear": "長槍",
    "short_sword": "短劍",
    "club": "棍棒",
    "wooden_stick": "木棍",
    "stone": "石頭",
}

# ---------------------------------------------------------------------------
# Unit 5 — Background Scene
# ---------------------------------------------------------------------------
BACKGROUND_OPTIONS: dict[str, list[str]] = {
    "S": ["palace_throne", "dragon_lair", "sky_city"],
    "A": ["castle", "magic_tower"],
    "B": ["town", "market"],
    "C": ["village", "wilderness"],
    "D": ["ruins"],
}

BACKGROUND_LABELS: dict[str, str] = {
    "palace_throne": "皇宮王座",
    "dragon_lair": "龍巢",
    "sky_city": "天空之城",
    "castle": "城堡",
    "magic_tower": "魔法塔",
    "town": "城鎮",
    "market": "市集",
    "village": "小村落",
    "wilderness": "荒野",
    "ruins": "破敗廢墟",
}

# ---------------------------------------------------------------------------
# Unit 6 — Expression, Pose, Border, Level
# ---------------------------------------------------------------------------
EXPRESSION_OPTIONS: dict[str, list[str]] = {
    "S": ["regal"],
    "A": ["passionate"],
    "B": ["confident"],
    "C": ["calm"],
    "D": ["weary"],
}

EXPRESSION_LABELS: dict[str, str] = {
    "regal": "王者風範",
    "passionate": "激昂",
    "confident": "自信",
    "calm": "平靜",
    "weary": "疲憊",
}

POSE_OPTIONS: dict[str, list[str]] = {
    "S": ["charging"],
    "A": ["battle_ready"],
    "B": ["standing"],
    "C": ["crouching"],
    "D": ["crouching"],
}

POSE_LABELS: dict[str, str] = {
    "charging": "衝鋒陷陣",
    "battle_ready": "持武器備戰",
    "standing": "站立",
    "crouching": "蹲坐",
}


# ===================================================================
# Public API
# ===================================================================


async def get_available_options(
    unit_code: str,
    quiz_score: float,
    completion_rate: float | None = None,
    *,
    character_class: str | None = None,
    db: AsyncSession | None = None,
) -> dict:
    """Return available RPG attribute options for a unit given scores.

    When *db* is provided, reads options from the attribute_rules table.
    Falls back to hardcoded constants if DB has no matching rules.

    Parameters
    ----------
    unit_code : str
        One of "unit_1" through "unit_6".
    quiz_score : float
        Quiz score (0-100), the primary driver for most attributes.
    completion_rate : float | None
        Completion rate (0-100), used by unit_2 for body type.
    character_class : str | None
        The student's chosen class (e.g. "mage"), used by unit_4 to
        filter weapon types by class affinity.
    db : AsyncSession | None
        If provided, query attribute_rules table. Otherwise use hardcoded.
    """
    if db is not None:
        result = await _get_available_options_from_db(
            db, unit_code, quiz_score,
            completion_rate=completion_rate,
            character_class=character_class,
        )
        if result:
            return result

    return _get_available_options_hardcoded(
        unit_code, quiz_score,
        completion_rate=completion_rate,
        character_class=character_class,
    )


async def _get_available_options_from_db(
    db: AsyncSession,
    unit_code: str,
    quiz_score: float,
    *,
    completion_rate: float | None = None,
    character_class: str | None = None,
) -> dict:
    """Query attribute_rules table and return options dict, or {} if no rules found."""
    from app.models.attribute_rule import AttributeRule

    tier = _tier(quiz_score)

    # For unit_2 body uses completion_rate tier
    tiers_to_query = {tier}
    if unit_code == "unit_2" and completion_rate is not None:
        cr_tier = _tier(completion_rate)
        tiers_to_query.add(cr_tier)

    result = await db.execute(
        select(AttributeRule).where(
            AttributeRule.unit_code == unit_code,
            AttributeRule.tier.in_(list(tiers_to_query)),
        )
    )
    rules = result.scalars().all()

    if not rules:
        return {}

    # Index rules by (attribute_type, tier)
    rule_map: dict[tuple[str, str], AttributeRule] = {}
    for rule in rules:
        rule_map[(rule.attribute_type, rule.tier)] = rule

    output: dict = {}

    # Build output for each attribute_type found
    attr_types = sorted({r.attribute_type for r in rules}, key=lambda a: next(
        (r.sort_order for r in rules if r.attribute_type == a), 0
    ))

    for attr_type in attr_types:
        # Determine which tier to use for this attribute
        if unit_code == "unit_2" and attr_type == "body" and completion_rate is not None:
            use_tier = _tier(completion_rate)
        else:
            use_tier = tier

        rule = rule_map.get((attr_type, use_tier))
        if rule is None:
            continue

        try:
            options = json.loads(rule.options)
            labels = json.loads(rule.labels)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid JSON in attribute_rule id=%s", rule.id)
            continue

        # Apply weapon class affinity filter for weapon_type
        if unit_code == "unit_4" and attr_type == "weapon_type" and character_class:
            if character_class in CLASS_WEAPON_AFFINITY:
                affinity = CLASS_WEAPON_AFFINITY[character_class]
                filtered = [w for w in options if w in affinity]
                if filtered:
                    options = filtered
                    labels = {k: v for k, v in labels.items() if k in options}

        output[attr_type] = {
            "options": options,
            "labels": labels,
        }

    return output


def _get_available_options_hardcoded(
    unit_code: str,
    quiz_score: float,
    completion_rate: float | None = None,
    *,
    character_class: str | None = None,
) -> dict:
    """Hardcoded fallback — original implementation."""
    tier = _tier(quiz_score)

    if unit_code == "unit_1":
        return _options_unit_1(tier)
    if unit_code == "unit_2":
        completion_tier = _tier(completion_rate) if completion_rate is not None else tier
        return _options_unit_2(tier, completion_tier)
    if unit_code == "unit_3":
        return _options_unit_3(tier)
    if unit_code == "unit_4":
        return _options_unit_4(tier, character_class)
    if unit_code == "unit_5":
        return _options_unit_5(tier)
    if unit_code == "unit_6":
        return _options_unit_6(tier)

    return {}


def calculate_card_level(overall_completion: float) -> int:
    """Map 0-100% overall completion to level 1-10."""
    clamped = max(0.0, min(100.0, overall_completion))
    level = math.ceil(clamped / 10)
    return max(1, level)


def determine_border_style(weeks_completed: int) -> str:
    """Return border style based on learning weeks completed.

    - 1-6 weeks  → copper
    - 7-12 weeks → silver
    - 13+ weeks  → gold
    """
    if weeks_completed >= 13:
        return "gold"
    if weeks_completed >= 7:
        return "silver"
    return "copper"


# ===================================================================
# Internal helpers
# ===================================================================


def _pick(options_map: dict[str, list[str]], labels_map: dict[str, str], tier: str) -> dict:
    """Build a standard options payload from a tier lookup."""
    opts = options_map[tier]
    return {
        "options": opts,
        "labels": {k: labels_map[k] for k in opts},
    }


def _options_unit_1(tier: str) -> dict:
    return {
        "race": _pick(RACE_OPTIONS, RACE_LABELS, tier),
        "gender": {
            "options": GENDER_OPTIONS,
            "labels": GENDER_LABELS,
        },
    }


def _options_unit_2(quiz_tier: str, completion_tier: str) -> dict:
    return {
        "class": _pick(CLASS_OPTIONS, CLASS_LABELS, quiz_tier),
        "body": _pick(BODY_OPTIONS, BODY_LABELS, completion_tier),
    }


def _options_unit_3(tier: str) -> dict:
    return {
        "equipment": _pick(EQUIPMENT_OPTIONS, EQUIPMENT_LABELS, tier),
    }


def _options_unit_4(tier: str, character_class: str | None) -> dict:
    quality = WEAPON_QUALITY[tier]
    base_types = WEAPON_TYPES_BY_TIER[tier]

    # Filter by class affinity if a class is known
    if character_class and character_class in CLASS_WEAPON_AFFINITY:
        affinity = CLASS_WEAPON_AFFINITY[character_class]
        filtered = [w for w in base_types if w in affinity]
        # Fallback: if nothing matches (e.g. low tier), keep base list
        weapon_types = filtered if filtered else base_types
    else:
        weapon_types = base_types

    return {
        "weapon_quality": {
            "options": [quality],
            "labels": {quality: WEAPON_QUALITY_LABELS[quality]},
        },
        "weapon_type": {
            "options": weapon_types,
            "labels": {k: WEAPON_TYPE_LABELS[k] for k in weapon_types},
        },
    }


def _options_unit_5(tier: str) -> dict:
    return {
        "background": _pick(BACKGROUND_OPTIONS, BACKGROUND_LABELS, tier),
    }


def _options_unit_6(tier: str) -> dict:
    return {
        "expression": _pick(EXPRESSION_OPTIONS, EXPRESSION_LABELS, tier),
        "pose": _pick(POSE_OPTIONS, POSE_LABELS, tier),
    }
