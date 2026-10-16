---
name: Bug report
description: Something in the stack or the scripts does not do what the README says
labels: ["bug"]
body:
  - type: markdown
    attributes:
      value: |
        The pool, the de-duplicator and the scripts are what this issue form is
        for. If the gateway answers `403 FreeTierError` for a request that looks
        correct to you, use the **gate behaviour observation** form instead --
        that is an upstream-behaviour question, not a bug in this repository,
        and it is far more likely to get you a useful answer.

  - type: textarea
    id: what
    attributes:
      label: What happened
      description: What you expected, and what you got. Include the exact error.
      render: text
    validations:
      required: true

  - type: textarea
    id: repro
    attributes:
      label: How to reproduce
      description: Commands, ideally from a clean state.
      render: shell
    validations:
      required: true

  - type: textarea
    id: logs
    attributes:
      label: Relevant output
      description: |
        `docker logs ofu-dedupe --tail 30` is usually the useful one. Please
        check it for keys or tokens before pasting.
      render: text
    validations:
      required: true

  - type: input
    id: env
    attributes:
      label: Environment
      description: Docker and Python versions, host OS, and whether the stack is already running.
      placeholder: Docker 27.x, python3.12, Debian 12

  - type: input
    id: commit
    attributes:
      label: Repo commit
      placeholder: a1b2c3d

  - type: checkboxes
    id: checks
    attributes:
      label: Before submitting
      options:
        - label: I ran `./scripts/smoke.sh` and pasted what it printed
          required: true
        - label: I checked the Troubleshooting section of the README
        - label: I did not paste any key, token, or password
---
