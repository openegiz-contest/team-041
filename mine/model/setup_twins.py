"""Create the mine's twins in Ditto. Safe to run any number of times.

A missing thing is created with its attributes and initial features. A thing
that already exists keeps its features, because the simulator is their only
writer; only its attributes are brought up to date with the model. Things left
in the namespace by an earlier version of the model are removed.

    python -m mine.model.setup_twins
"""
from __future__ import annotations

import logging
import os
import sys

from mine.common.ditto import DittoClient, DittoError
from mine.model.build_twins import build_things, load_world

log = logging.getLogger("mine.setup")

# Namespaces of earlier versions of the model: their twins are removed, so that a stand upgraded in place
# does not keep a second, frozen copy of the mine.
LEGACY_NAMESPACES = ("kz.aqtas",)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    password = os.environ.get("DITTO_PASSWORD")
    if not password:
        log.error("DITTO_PASSWORD is not set")
        return 2
    ditto = DittoClient(os.environ.get("DITTO_URL", "http://ditto-nginx:80"),
                        os.environ.get("DITTO_USER", "ditto"), password)
    world = load_world()
    things = build_things(world)  # parents come before their children
    try:
        created = 0
        for thing in things:
            if ditto.create_thing(thing):
                created += 1
                log.info("created %s", thing["thingId"])
            else:
                ditto.put_attributes(thing["thingId"], thing["attributes"])
        wanted = {t["thingId"] for t in things}
        stale = [tid for tid in ditto.search_ids(world["namespace"]) if tid not in wanted]
        for namespace in LEGACY_NAMESPACES:
            if namespace != world["namespace"]:
                stale += ditto.search_ids(namespace)
        for tid in stale:
            ditto.delete_thing(tid)
            log.info("removed %s: no longer in the model", tid)
    except DittoError as exc:
        log.error("twin setup failed: %s", exc)
        return 1
    log.info("%d twins in the model: %d created, %d already present, %d stale removed",
             len(things), created, len(things) - created, len(stale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
