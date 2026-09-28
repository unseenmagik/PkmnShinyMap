#!/usr/bin/env python3
"""Regenerate shinyPossible.json from Leek Duck and Pokemon GO Hub shiny lists.

Sources (a shiny from either counts; if one site is down the other is used):
  - https://leekduck.com/shiny/pms.json  (the data behind https://leekduck.com/shiny/)
      Has release dates, so shinies dated in the future are held back even if
      GO Hub already lists them.
  - https://db.pokemongohub.net/tools/shiny-checklist
      Quicker to add new releases, but skips some multi-form species (Unown,
      Spinda, Vivillon...) and has no dates. Parsed from the sprite file names.
  - WatWowMap masterfile                  (form proto names -> Poracle form ids)

Poracle treats a key as "shiny possible" if it exists in the map, checking
"<pokemonId>_<formId>" first and then "<pokemonId>". So a species gets a bare
id key only when every one of its forms is shiny; otherwise each shiny form is
listed individually.

Manual tweaks go in scripts/overrides.json:
  "include": keys to always add (e.g. "808")
  "exclude": keys to always drop, or a bare id to drop the whole species
"""

import datetime
import json
import os
import re
import sys
import urllib.request

LEEKDUCK_URL = "https://leekduck.com/shiny/pms.json"
GOHUB_URL = "https://db.pokemongohub.net/tools/shiny-checklist"
GOHUB_SPRITE = re.compile(r"/images/ingame/[a-z]+/(pm\d+[A-Za-z0-9_.]*?)\.s\.icon\.png")
MASTERFILE_URL = "https://raw.githubusercontent.com/WatWowMap/Masterfile-Generator/master/master-latest-everything.json"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "shinyPossible.json")
OVERRIDES = os.path.join(ROOT, "scripts", "overrides.json")

SPARKLE = " ✨"
# Forms that exist only as temporary evolutions (not Poracle form ids)
IGNORED_LEEKDUCK_FORMS = {"MEGA", "MEGA_X", "MEGA_Y", "PRIMAL", "GIGANTAMAX"}
# Proto forms that follow the base form's shiny status
BASE_LIKE_SUFFIXES = ("_NORMAL", "_SHADOW", "_PURIFIED")
# Sanity floor per source, so a broken page can't wipe the list
MIN_SOURCE_ENTRIES = 500


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PkmnShinyMap updater)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def fetch_json(url):
    return json.loads(fetch(url))


def strip_gender(aa_fn):
    return ".".join(p for p in aa_fn.split(".") if not p.startswith("g"))


def leekduck_shinies(today):
    """-> (released asset names, asset names with a future release date)"""
    entries = fetch_json(LEEKDUCK_URL)
    if not isinstance(entries, list) or len(entries) < MIN_SOURCE_ENTRIES:
        raise ValueError(f"unexpected data ({len(entries) if isinstance(entries, list) else 0} entries)")
    released, upcoming = set(), set()
    for entry in entries:
        date = datetime.date(*map(int, entry["released_date"].split("/")))
        (upcoming if date > today else released).add(strip_gender(entry["aa_fn"]))
    return released, upcoming


def gohub_shinies():
    names = {strip_gender(m) for m in GOHUB_SPRITE.findall(fetch(GOHUB_URL))}
    if len(names) < MIN_SOURCE_ENTRIES:
        raise ValueError(f"unexpected data ({len(names)} sprites)")
    return names


def parse_aa_fn(aa_fn):
    """'pm80.fGALARIAN.cFOO.g2' -> form 'GALARIAN' (or None), costume codes ['FOO']."""
    form, costume = None, []
    for part in aa_fn.split(".")[1:]:
        if part.startswith("f"):
            form = part[1:]
        elif part.startswith("c"):
            costume.append(part[1:])
    return form, costume


def shiny_codes(asset_names):
    """dex -> set of form codes (None = base form) with shiny released."""
    shinies = {}
    for aa_fn in asset_names:
        form, costume = parse_aa_fn(aa_fn)
        if form in IGNORED_LEEKDUCK_FORMS:
            continue
        if form == "NORMAL":
            form = None
        codes = shinies.setdefault(int(aa_fn.split(".")[0][2:]), set())
        # Some costumes are separate form ids in the masterfile (e.g. PIKACHU_FALL_2019)
        codes.update(c.removesuffix("_NOEVOLVE") for c in costume)
        if not costume:
            codes.add(form)
    return shinies


def is_base_form(form_id, proto, default_form_id):
    return form_id == 0 or form_id == default_form_id or proto.endswith(BASE_LIKE_SUFFIXES)


def form_matches(proto, code):
    return proto == code or proto.endswith("_" + code)


def build_map(shinies, pokemon, overrides):
    out = {}
    exclude = set(overrides.get("exclude", []))
    for dex in sorted(shinies):
        if str(dex) in exclude:
            continue
        codes = shinies[dex]
        mon = pokemon.get(str(dex))
        forms = mon.get("forms", {}) if mon else {}
        default_form_id = mon.get("defaultFormId") if mon else None

        shiny_ids, missing_names, base_ids = [], [], []
        for form_id_str, form in forms.items():
            form_id, proto = int(form_id_str), form["proto"]
            if any(form_matches(proto, c) for c in codes if c):
                shiny_ids.append(form_id)
            elif is_base_form(form_id, proto, default_form_id):
                base_ids.append(form_id)
            else:
                missing_names.append(form.get("name", proto))

        # Base/placeholder forms (Unset, Normal, Shadow, Purified) follow the base shiny;
        # for species with no plain base (Burmy, Shellos, ...) they follow any shiny form.
        base_shiny = None in codes or (bool(shiny_ids) and not any(
            i != 0 and i == default_form_id for i in base_ids))
        if base_shiny:
            shiny_ids += base_ids
        else:
            missing_names += [forms[str(i)].get("name", "") for i in base_ids]
        if not shiny_ids and None not in codes:
            continue

        if not forms or not missing_names:
            out[str(dex)] = SPARKLE
            continue

        name = mon["name"]
        shiny_names = [forms[str(i)].get("name", "") for i in shiny_ids if i != 0]
        out[f"***{name} - Shiny: {', '.join(shiny_names)} (Not: {', '.join(missing_names)})***"] = ""
        for form_id in sorted(shiny_ids):
            out[f"{dex}_{form_id}"] = SPARKLE

    for key in exclude:
        out.pop(key, None)
    for key in overrides.get("include", []):
        out[key] = SPARKLE
    return out


def sort_key(key):
    if key.startswith("***"):
        return (0, 0, 0)  # comments keep their position via stable ordering below
    dex, _, form = key.partition("_")
    return (int(dex), 1, int(form) if form else -1)


def ordered(entries):
    """Sort by dex, keeping each comment directly above its species' form keys."""
    groups, pending = [], None
    for key, value in entries.items():
        if key.startswith("***"):
            pending = (key, value)
            continue
        if pending:
            groups.append((sort_key(key), [pending, (key, value)]))
            pending = None
        else:
            groups.append((sort_key(key), [(key, value)]))
    groups.sort(key=lambda g: g[0])
    return {k: v for _, pairs in groups for k, v in pairs}


def main():
    today = datetime.datetime.now(datetime.timezone.utc).date()
    released, upcoming, sources = set(), set(), 0
    try:
        leek_released, upcoming = leekduck_shinies(today)
        released |= leek_released
        sources += 1
        print(f"Leek Duck: {len(leek_released)} released, {len(upcoming)} upcoming")
    except Exception as err:
        print(f"::warning::Leek Duck failed: {err}")
    try:
        hub = gohub_shinies()
        new = hub - released - upcoming
        released |= hub - upcoming
        sources += 1
        print(f"GO Hub: {len(hub)} sprites, {len(new)} not on Leek Duck: {', '.join(sorted(new))}")
    except Exception as err:
        print(f"::warning::GO Hub failed: {err}")
    if not sources:
        sys.exit("No shiny source available; not updating")
    pokemon = fetch_json(MASTERFILE_URL)["pokemon"]

    overrides = {}
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, encoding="utf-8") as f:
            overrides = json.load(f)

    shiny_map = ordered(build_map(shiny_codes(released), pokemon, overrides))
    result = {"name": "shinyPossible", "map": shiny_map}
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=3)
        f.write("\n")
    species = len({k.split("_")[0] for k in shiny_map if not k.startswith("***")})
    print(f"Wrote {OUTPUT}: {species} species, {len(shiny_map)} keys")


if __name__ == "__main__":
    main()
