"""Every sentence the robot says or the editor shows, in English and Czech.

ROBOT_LANG (shell or .env) picks the language: en (default) or cs.

The gate's reasons are fixed templates here, not translations made by the
model on the fly. The model only reads them out, so what the robot says
about safety is exactly what this file says — in either language.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

SUPPORTED = ("en", "cs")


def _from_env() -> str:
    code = os.environ.get("ROBOT_LANG", "en").strip().lower()[:2]
    return code if code in SUPPORTED else "en"


_lang = _from_env()


def language() -> str:
    return _lang


def set_language(code: str) -> None:
    """For tests; the running system takes ROBOT_LANG at startup."""
    global _lang
    if code not in SUPPORTED:
        raise ValueError(f"ROBOT_LANG must be one of {SUPPORTED}, not {code!r}")
    _lang = code


def t(key: str, **params) -> str:
    return MESSAGES[key][_lang].format(**params)


def num(value: float, digits: int = 1) -> str:
    """A number as it is written in the current language: 11.4 or 11,4."""
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if _lang == "cs" else text


def percent(share: float) -> str:
    """0.9 -> '90%' in English, '90 %' in Czech."""
    return f"{share:.0%}" if _lang == "en" else f"{share * 100:.0f} %"


def seconds_ago(n: int) -> str:
    """'12 seconds' / '12 sekundami' — the one place Czech needs a plural."""
    if _lang == "cs":
        return f"{n} sekundou" if n == 1 else f"{n} sekundami"
    return f"{n} second" if n == 1 else f"{n} seconds"


MESSAGES: dict[str, dict[str, str]] = {
    # ---- the gate: what the robot says when it refuses or slows down -------
    "gate.state_age": {
        "en": "I have not heard from the robot for {age} seconds.",
        "cs": "Od robota nemám zprávu už {age} sekundy.",
    },
    "gate.lost": {
        "en": "The robot does not know where it is on the map.",
        "cs": "Robot neví, kde na mapě je.",
    },
    "gate.map": {
        "en": "The robot is using a different map from the one its forbidden "
              "zones were drawn on, so I cannot tell where they are. {detail}",
        "cs": "Robot používá jinou mapu, než na které jsou nakreslené zakázané "
              "zóny, takže nevím, kde leží. {detail}",
    },
    "gate.battery_to_charger": {
        "en": "Battery is low at {v} volts, so I will drive to the charger slowly.",
        "cs": "Baterie je slabá, má {v} voltu, takže k nabíječce pojedu pomalu.",
    },
    "gate.battery_stop": {
        "en": "Battery is down to {v} volts. The only trip I can make is to the charger.",
        "cs": "Baterie má už jen {v} voltu. Jediná cesta, kterou můžu udělat, je k nabíječce.",
    },
    "gate.battery_no_charger": {
        "en": "Battery is down to {v} volts, too low to drive, and no charger is marked on this map.",
        "cs": "Baterie má už jen {v} voltu, to je na jízdu málo, a na téhle mapě není označená nabíječka.",
    },
    "gate.battery_limit": {
        "en": "Battery is at {v} volts, so I will drive at half speed.",
        "cs": "Baterie má {v} voltu, takže pojedu poloviční rychlostí.",
    },
    "gate.no_route": {
        "en": "I could not find a route to that place.",
        "cs": "K tomu místu jsem nenašel cestu.",
    },
    "gate.zone_goal": {
        "en": "That destination is inside {zone}, which is off limits.",
        "cs": "Cíl leží v zóně „{zone}“, kam nesmím.",
    },
    "gate.zone_route": {
        "en": "The route passes through {zone}, which is off limits.",
        "cs": "Trasa vede přes zónu „{zone}“, kam nesmím.",
    },
    "gate.too_tight": {
        "en": "The narrowest point on the route is {c} metres, too tight for me to fit.",
        "cs": "Nejužší místo na trase má {c} metru, tím se neprotáhnu.",
    },
    "gate.narrow": {
        "en": "The route narrows to {c} metres, so I will drive at half speed.",
        "cs": "Trasa se zužuje na {c} metru, takže pojedu poloviční rychlostí.",
    },
    "gate.obstacle": {
        "en": "Something is {d} metres away from me, too close to start moving.",
        "cs": "Něco je ode mě jen {d} metru, to je moc blízko na rozjezd.",
    },

    # ---- the tools: what the robot answers ---------------------------------
    "tool.unknown_place": {
        "en": "I do not know a place called {place}. I know: {known}.",
        "cs": "Místo „{place}“ neznám. Znám tato místa: {known}.",
    },
    "tool.status_lost": {
        "en": "The robot is powered up but does not know where it is.",
        "cs": "Robot je zapnutý, ale neví, kde je.",
    },
    "tool.status": {
        "en": "Battery {v} volts. The robot is {doing} at {x}, {y} metres. "
              "Nearest obstacle {d} metres.",
        "cs": "Baterie má {v} voltu. Robot {doing}, souřadnice {x} a {y} metru. "
              "Nejbližší překážka je {d} metru daleko.",
    },
    "tool.driving_now": {"en": "driving", "cs": "jede"},
    "tool.stopped_now": {"en": "stopped", "cs": "stojí"},
    "tool.places": {
        "en": "I can drive to: {places}.",
        "cs": "Můžu jet na tato místa: {places}.",
    },
    "tool.no_places": {
        "en": "No places have been set up on this map yet.",
        "cs": "Na téhle mapě zatím nejsou žádná místa.",
    },
    "tool.refuse": {
        "en": "I cannot make that trip. {reasons}",
        "cs": "Tuhle cestu udělat nemůžu. {reasons}",
    },
    "tool.offer": {
        "en": "I can drive to {name}.{warning} Confirm with token {token} within 30 seconds.",
        "cs": "Můžu jet na místo „{name}“.{warning} Potvrď to tokenem {token} do 30 sekund.",
    },
    "tool.changed": {
        "en": "Something changed while we were talking, so I am staying put. {reasons}",
        "cs": "Mezitím se něco změnilo, takže zůstávám stát. {reasons}",
    },
    "tool.driving_half": {
        "en": "Driving to {name} at half speed.",
        "cs": "Jedu na místo „{name}“ poloviční rychlostí.",
    },
    "tool.driving": {
        "en": "Driving to {name}.",
        "cs": "Jedu na místo „{name}“.",
    },
    "tool.destination": {"en": "the destination", "cs": "cíl"},
    "tool.stopped": {"en": "Stopped.", "cs": "Stojím."},
    "tool.no_stop_yet": {
        "en": "I have not stopped for anything since we started.",
        "cs": "Od začátku jsem kvůli ničemu nezastavil.",
    },
    "tool.last_stop": {
        "en": "About {ago} ago{where}: {reasons}",
        "cs": "Zhruba před {ago}{where}: {reasons}",
    },
    "tool.last_stop_where": {
        "en": " on the way to {name}",
        "cs": " cestou na místo „{name}“",
    },
    "tool.nothing_yet": {"en": "Nothing has happened yet.", "cs": "Zatím se nic nestalo."},
    "token.invalid": {
        "en": "That confirmation is no longer valid. Ask me again and I will "
              "work out a fresh route.",
        "cs": "Tohle potvrzení už neplatí. Zeptej se znovu a naplánuju novou cestu.",
    },
    "agent.stuck": {
        "en": "I got stuck working that out. Try again.",
        "cs": "Tohle se mi nepodařilo vyřešit. Zkus to prosím znovu.",
    },

    # ---- is the robot on the same map? -------------------------------------
    "map.no_map": {
        "en": "This site has no map to compare against.",
        "cs": "Tahle lokace nemá mapu, se kterou by se dalo porovnávat.",
    },
    "map.resolution": {
        "en": "The robot's map has {theirs} m cells, this one {mine} m.",
        "cs": "Mapa robota má buňky {theirs} m, tahle {mine} m.",
    },
    "map.size": {
        "en": "The robot's map is {theirs} cells, this one {mine}.",
        "cs": "Mapa robota má {theirs} buněk, tahle {mine}.",
    },
    "map.origin": {
        "en": "The two maps start at different origins.",
        "cs": "Obě mapy začínají v jiném bodě.",
    },
    "map.length": {
        "en": "The robot sent a map of the wrong length.",
        "cs": "Robot poslal mapu se špatnou délkou.",
    },
    "map.agree": {
        "en": "Only {share} of the two maps agree.",
        "cs": "Obě mapy se shodují jen ze {share}.",
    },
    "map.simulated": {
        "en": "Simulated robot: no robot map to compare.",
        "cs": "Simulovaný robot: není s čím porovnat.",
    },
    "map.unverified": {
        "en": "Could not read the map from the robot's map_server, so the zones "
              "are not verified.",
        "cs": "Mapu z map_serveru robota se nepodařilo přečíst, takže zóny "
              "nejsou ověřené.",
    },
    "map.match": {
        "en": "The robot is using this map.",
        "cs": "Robot používá tuhle mapu.",
    },

    # ---- sites: what the editor refuses, and why ---------------------------
    "site.bad_folder": {
        "en": "SITE must be a plain folder name, not {name!r}",
        "cs": "SITE musí být obyčejný název složky, ne {name!r}",
    },
    "site.missing": {
        "en": "No site at {folder} (missing site.json)",
        "cs": "Ve složce {folder} není žádná lokace (chybí site.json)",
    },
    "site.bad_json": {
        "en": "{path} is not valid JSON: {error}",
        "cs": "{path} není platný JSON: {error}",
    },
    "site.not_object": {
        "en": "A site must be a JSON object.",
        "cs": "Lokace musí být objekt JSON.",
    },
    "site.not_lists": {
        "en": "places and zones must be lists.",
        "cs": "places a zones musí být seznamy.",
    },
    "site.too_many_places": {
        "en": "At most {n} places.",
        "cs": "Nejvýš {n} míst.",
    },
    "site.too_many_zones": {
        "en": "At most {n} zones.",
        "cs": "Nejvýš {n} zón.",
    },
    "site.place_shape": {
        "en": "Each place must be an object with name, x and y.",
        "cs": "Každé místo musí mít name, x a y.",
    },
    "site.charger_flag": {
        "en": "Place {name!r}: \"charger\" must be true or false.",
        "cs": "Místo „{name}“: \"charger\" musí být true nebo false.",
    },
    "site.two_chargers": {
        "en": "Two places are marked as the charger: {a!r} and {b!r}. Mark only one.",
        "cs": "Jako nabíječka jsou označená dvě místa: „{a}“ a „{b}“. Označ jen jedno.",
    },
    "site.place_twice": {
        "en": "Two places are called {name!r}.",
        "cs": "Dvě místa se jmenují „{name}“.",
    },
    "site.zone_shape": {
        "en": "Each zone must be an object with name and polygon.",
        "cs": "Každá zóna musí mít name a polygon.",
    },
    "site.zone_twice": {
        "en": "Two zones are called {name!r}.",
        "cs": "Dvě zóny se jmenují „{name}“.",
    },
    "site.zone_kind": {
        "en": "Zone {name!r} has unknown kind {kind!r}.",
        "cs": "Zóna „{name}“ má neznámý druh {kind!r}.",
    },
    "site.zone_corners": {
        "en": "Zone {name!r} needs between 3 and {n} corners.",
        "cs": "Zóna „{name}“ potřebuje 3 až {n} rohů.",
    },
    "site.zone_corner_shape": {
        "en": "Zone {name!r} has a corner that is not [x, y].",
        "cs": "Zóna „{name}“ má roh, který není [x, y].",
    },
    "site.zone_crosses": {
        "en": "Zone {name!r} crosses itself, so inside and outside are ambiguous.",
        "cs": "Zóna „{name}“ kříží sama sebe, takže není jasné, co je uvnitř a co venku.",
    },
    "site.zone_small": {
        "en": "Zone {name!r} is too small to mean anything.",
        "cs": "Zóna „{name}“ je příliš malá, aby k něčemu byla.",
    },
    "site.map_field": {
        "en": "map must be the name of a .yaml file.",
        "cs": "map musí být název souboru .yaml.",
    },
    "site.name_missing.site": {"en": "Every site needs a name.", "cs": "Každá lokace potřebuje jméno."},
    "site.name_missing.place": {"en": "Every place needs a name.", "cs": "Každé místo potřebuje jméno."},
    "site.name_missing.zone": {"en": "Every zone needs a name.", "cs": "Každá zóna potřebuje jméno."},
    "site.name_long.site": {
        "en": "The site name {name!r} is longer than {n} characters.",
        "cs": "Jméno lokace „{name}“ je delší než {n} znaků.",
    },
    "site.name_long.place": {
        "en": "The place name {name!r} is longer than {n} characters.",
        "cs": "Jméno místa „{name}“ je delší než {n} znaků.",
    },
    "site.name_long.zone": {
        "en": "The zone name {name!r} is longer than {n} characters.",
        "cs": "Jméno zóny „{name}“ je delší než {n} znaků.",
    },
    "site.where.place": {"en": "place {name!r}", "cs": "místa „{name}“"},
    "site.where.zone": {"en": "zone {name!r}", "cs": "zóny „{name}“"},
    "site.coords_numbers": {
        "en": "Coordinates of {where} must be numbers.",
        "cs": "Souřadnice {where} musí být čísla.",
    },
    "site.coords_finite": {
        "en": "Coordinates of {where} must be finite.",
        "cs": "Souřadnice {where} musí být konečná čísla.",
    },
    "site.outside.place": {
        "en": "Place {name!r} lies outside the map.",
        "cs": "Místo „{name}“ leží mimo mapu.",
    },
    "site.outside.zone": {
        "en": "Zone {name!r} lies outside the map.",
        "cs": "Zóna „{name}“ leží mimo mapu.",
    },
    "site.exists": {
        "en": "A site called {name!r} already exists.",
        "cs": "Lokace „{name}“ už existuje.",
    },
    "site.unsurveyed": {
        "en": "That map is entirely unknown: nothing was surveyed.",
        "cs": "Ta mapa je celá neznámá: nic na ní nebylo zmapováno.",
    },
    "site.slug": {
        "en": "The name needs at least one letter or digit.",
        "cs": "Jméno musí obsahovat aspoň jedno písmeno nebo číslici.",
    },
    "site.no_map": {"en": "This site has no map.", "cs": "Tahle lokace nemá mapu."},
    "site.map_missing": {
        "en": "Map file {path} not found",
        "cs": "Soubor mapy {path} nebyl nalezen",
    },
    "site.map_resolution": {
        "en": "{path}: resolution must be positive",
        "cs": "{path}: resolution musí být kladné",
    },
    "site.map_key": {
        "en": "Map YAML has no {key!r}",
        "cs": "V YAML mapy chybí {key!r}",
    },
    "site.image_missing": {
        "en": "Map image {path} not found",
        "cs": "Obrázek mapy {path} nebyl nalezen",
    },
    "site.not_pgm": {
        "en": "{path} is not a binary PGM (P5)",
        "cs": "{path} není binární PGM (P5)",
    },
    "site.pgm_16bit": {
        "en": "{path} uses 16-bit pixels, which map_saver never writes",
        "cs": "{path} má 16bitové pixely, které map_saver nikdy nezapisuje",
    },
    "site.pgm_short": {
        "en": "{path} is shorter than its header says",
        "cs": "{path} je kratší, než udává jeho hlavička",
    },

    # ---- operator routes ----------------------------------------------------
    "op.not_json": {"en": "The request was not JSON.", "cs": "Požadavek nebyl JSON."},
    "op.not_object": {
        "en": "The request must be a JSON object.",
        "cs": "Požadavek musí být objekt JSON.",
    },
    "op.conflict": {
        "en": "The map changed while you were editing. Reload it and draw your "
              "change again.",
        "cs": "Mapa se mezitím změnila. Načti ji znovu a změnu nakresli ještě jednou.",
    },
    "op.real_robot": {
        "en": "With a real robot connected, start the server with SITE=... "
              "instead of switching maps here.",
        "cs": "Se skutečným robotem se lokace vybírá při startu serveru "
              "(SITE=...), ne tady.",
    },
    "op.send_map": {
        "en": "Send the map's name, its .yaml text and its .pgm file.",
        "cs": "Pošli jméno mapy, text jejího .yaml a soubor .pgm.",
    },
    "op.which_language": {
        "en": "Language must be one of: {codes}.",
        "cs": "Jazyk musí být jeden z: {codes}.",
    },
    "op.which_site": {
        "en": "Say which site to switch to.",
        "cs": "Řekni, na kterou lokaci přepnout.",
    },
}
