---
name: Gate behaviour observation
description: The free-tier gates changed upstream, or you found a request shape that is refused
labels: ["gate-observation"]
body:
  - type: markdown
    attributes:
      value: |
        The gates this repository documents are reverse-engineered behaviour of
        an undocumented upstream. They change without notice. If you have seen
        one behave differently, that is the most valuable thing you can report
        here, and it does not need a reproduction on your side.

        Please include the negative control. A request that succeeds proves
        nothing on its own; what identifies a gate is a pair -- one request that
        works and one that differs in exactly one field and does not.

  - type: textarea
    id: baseline
    attributes:
      label: Baseline request that works
      description: The request that returns 200. Paste it verbatim, headers included.
      render: text
    validations:
      required: true

  - type: textarea
    id: negative
    attributes:
      label: Request that is refused
      description: |
        What differs from the baseline? Exactly one field, ideally.
        `scripts/gate-probe.py` varies one field at a time for exactly this
        reason.
      render: text
    validations:
      required: true

  - type: textarea
    id: responses
    attributes:
      label: Both response bodies and status codes
      description: |
        Especially the error body -- `403 FreeTierError` says a lot more than
        the status code does. Trim anything that identifies you.
      render: text
    validations:
      required: true

  - type: input
    id: date
    attributes:
      label: When
      placeholder: 2026-10-16
    validations:
      required: true

  - type: input
    id: client
    attributes:
      label: Client and engine version
      description: OpenCode version, and the pinned engine image if you know it.
      placeholder: opencode 1.10.0 / opencode2api v1.4.1

  - type: dropdown
    id: change
    attributes:
      label: Direction
      options:
        - Previously refused, now accepted
        - Previously accepted, now refused
        - Never documented here
        - Not sure
    validations:
      required: true

  - type: textarea
    id: notes
    attributes:
      label: Anything else
      description: Egress address, region, whether it reproduced more than once.
      render: text
---
