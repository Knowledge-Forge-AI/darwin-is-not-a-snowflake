# Additive stock browser acquisition

Status: additive selector decision preserved as historical. Stock Brave provider
and shared-suite statements below are superseded by the
[stock application runtime repair](../../../stock-app-runtime-repair.md).
Native qualification and host installation remain pending.

## Decision and scope

Core selects `programs.browserBaseline.stockApplications` only for
example-primary in its host constructor. It adds stock `pkgs.brave` and
Homebrew casks `librewolf` and `orion`. The same selected Brave package serves
the retained Brave YouTube wrapper; both identities carry intentional suite
claims. Core owns acquisition and the three new application claims.

This supersedes the acquisition statements in ADR 0001 that both hosts gain
Brave/LibreWolf, and separates acquisition from the held policy transition in
ADR 0002. example-workstation retains its preceding inventory. Generic module and
inventory consumers default to inactive acquisition; explicit acquisition
selects the same apps on either surface regardless of host name. The existing
R3 inventory selector retains its target-host gate for compatibility.

The constructor asserts the host selection. Selector-priority checks reject
overrides, and the duplicate-Brave-cask guard applies whenever core selects
stock Brave, independently of policy ownership. Policy writer guards remain
scoped to policy ownership. Acquisition grants no new policy-writing authority.

`browserRolesR3Selected`, `rolesR3` and `corePolicyOwnership` remain false for
the configured hosts. Their advanced deployment, activation and automation
holds remain in force. Acquisition changes no profile, credential, extension,
native-messaging or convenience configuration. Chrome and Firefox remain
installed with retirement required and unfinished. Browser UX retains Floorp
and convenience ownership and the Vivaldi visual reference.

## Verification and deployment boundary

Focused tests cover acquisition and claims, disabled/default consumers,
predecessor preservation, conflicting Brave casks, selector priority and held
policy effects. Connected core and composed host evaluations, registered check
builders and selected Brave realization require native daemon access. Fixtures
and declarations do not prove visible installation, security acceptance or
launch. Live Homebrew disabled/deprecated status, platform requirements and
caveats must be checked before deployment; an unavailable cask stops deployment
without a packaging or macOS security bypass.

Owner adoption and deployment use maintained DINAS source and plan commands.
All three visible bundles, installed IDs/versions, read-only macOS security
checks and attended launches are required to accept installation. Rollback is
an owner-reviewed selection change and ordinary deployment, preserving all
browser data. Acquisition does not wait for credential qualification or
Chrome/Firefox retirement.
