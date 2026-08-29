# Optional SOPS control plane

This directory preserves the encrypted-Git deployment workflow. It is not used
by the default single-host scripts in the parent `ops/` directory.

Activate it only as a complete control plane: prepare `ops-bootstrap.env`, stage
and activate an exact secret-config SHA, install the SOPS systemd units, and use
the deploy/rollback/restore scripts from this directory thereafter. Do not mix
these units with the default runtime-env units in one rollout.

See the “Optional SOPS control plane” section in `ops/README.md` for commands,
credential boundaries, rotation, and recovery guidance.
