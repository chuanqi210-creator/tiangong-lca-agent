# Security policy

## Supported release

Security fixes target the latest public release manifest on the default branch.
The manifest identifies the exact source basis and contracts covered by a
report. Historical private evidence is not part of the public support surface.

## Reporting a vulnerability

Do not open a public issue for a suspected secret, authorization bypass,
cross-project data exposure, unsafe archive behavior, or external-effect flaw.
Contact the repository owner through GitHub's private vulnerability reporting
channel. Include the public release ID, affected endpoint or module, minimal
reproduction, and observed impact. Do not include real user materials or
credentials.

## Boundaries

Public pull requests run without provider or deployment secrets. Model and tool
outputs remain candidates until the LCA Domain Kernel and Assurance accept
them. External writes, publication, payment, credentials, and irreversible
actions require the configured human responsibility boundary.
