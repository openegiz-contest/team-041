"""The decision modules of the Bozshakol mine model.

Each module is a service of its own (compose services `mine-dispatch`,
`mine-grade`, `mine-plant`, `mine-maint`, `mine-advisor`) built from two parts:

    <name>/core.py   the decision logic: a pure object that is told what the
                     twins report and answers with live messages to send
    runtime.py       the shell around it: reads the twins from the Ditto HTTP
                     API, follows their changes on MQTT, delivers the core's
                     messages through Ditto and publishes the module's own twin

A module never looks into the simulator. Everything it knows comes from the
platform, and everything it does is a Ditto live message to a twin
(docs/mine/ARCHITECTURE.md, section 5.2).

The first four steer the mine. The advisor steers nothing by itself: on an
incident it has the simulator forecast its response options and ranks them,
and sends an option's messages when an operator applies it (docs/mine/ARCHITECTURE.md, section 7).
"""
