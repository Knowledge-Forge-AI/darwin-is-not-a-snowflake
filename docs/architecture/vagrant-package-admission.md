# Vagrant package admission

Core admits the exact `vagrant` package name through its existing
`allowUnfreePredicate`. This permits an optional module to consume Vagrant from
core's package set. All earlier admissions remain; unrelated names and names
such as `vagrant-extra` remain denied. Versioned package names are normalized
by `lib.getName` as before.

Admission does not add Vagrant to core's default system packages or change
upstream licensing. Core remains independently evaluable, with no VM leaf or
composition input, import, or test dependency. Optional consumers own their
command packages and must explicitly enable bundled tooling.

The focused native Nix unit test imports the actual policy, checks existing and
new admissions and denial cases, and forces the real core Vagrant derivation.
Separate temporary integration qualification evaluates a consumer in dormant,
enabled without tooling, and enabled with tooling modes, builds the bundled
command, and exercises only its help output with disposable user directories.
That evidence belongs to the qualification report, not core's runtime inputs.

Package evaluation and command builds do not establish host activation, VMware
provider installation, VM boot, a usable guest image, or a KDE session. Core
publication also does not update a composition's selected core revision. Image
construction and interactive guest qualification remain separate work.
