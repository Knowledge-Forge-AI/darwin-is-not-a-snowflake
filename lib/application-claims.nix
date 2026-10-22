# Reusable application-claim contract. ADR 0002 section 3 is normative for this
# file. Every layer that aggregates claims validates through these helpers so
# the detection algorithm has exactly one implementation.
{ lib }:

let
  providers = [ "homebrew-cask" "mas" "nix" ];

  requiredClaimKeys = [ "artifact" "id" "package" "provider" ];
  optionalClaimKeys = [ "handoff" "suite" ];
  knownClaimKeys = requiredClaimKeys ++ optionalClaimKeys;
  requiredHandoffKeys = [ "adr" "from" ];

  sortUnique = values: lib.sort (a: b: a < b) (lib.unique values);
  joinLines = lib.concatStringsSep "\n";
  joinTokens = lib.concatStringsSep ", ";

  matches = pattern: value:
    builtins.isString value && builtins.match pattern value != null;

  # Bundle identifiers are reverse-DNS. Requiring a second segment rejects a
  # display name pasted in where an identifier belongs: "bundle:Firefox".
  bundleIdPattern = "[A-Za-z0-9][A-Za-z0-9-]*(\\.[A-Za-z0-9][A-Za-z0-9-]*)+";
  masIdPattern = "[0-9]+";

  # An artifact is a bare bundle file name, matched against its normalized form
  # so that spelling case never decides whether a bundle is well formed.
  # Forbidding "/" is what keeps an operator home directory out of a
  # publication-capable claim.
  artifactPattern = "[^/:.][^/:]*\\.app";

  normalizeId = lib.toLower;
  normalizeArtifact = lib.toLower;
  normalizePackage = lib.toLower;

  attachOwner = owner: claims:
    if !(builtins.isString owner) || owner == ""
    then throw "application claim owner must be a non-empty registry name"
    else if lib.hasInfix "*" owner
    then throw "application claim owner \"${owner}\" must be a literal registry name, not a pattern"
    else if !(builtins.isList claims)
    then throw "application claims for owner \"${owner}\" must be a list"
    else map (claim: { inherit owner claim; }) claims;

  describeRawClaim = owner: claim:
    let
      field = name:
        if builtins.isAttrs claim && claim ? ${name} && builtins.isString claim.${name}
        then claim.${name}
        else "<missing-or-invalid>";
    in
    "owner=${owner} id=${field "id"} provider=${field "provider"} "
    + "package=${field "package"} artifact=${field "artifact"}";

  schemaError = context: rule: detail:
    "application claim schema violation (${rule})\n\n  ${context}\n\n  ${detail}";

  handoffErrors = context: err: handoff:
    let
      keys = builtins.attrNames handoff;
      unknown = lib.subtractLists requiredHandoffKeys keys;
      missing = lib.subtractLists keys requiredHandoffKeys;
      stringErrors = name:
        let value = handoff.${name}; in
        lib.optional (!(builtins.isString value) || value == "")
          (err "handoff-${name}-empty" "handoff.${name} must be a non-empty string");
    in
    if !(builtins.isAttrs handoff)
    then [ (err "handoff-not-an-attribute-set" "handoff must be an attribute set") ]
    else if unknown != [ ] || missing != [ ]
    then [
      (err "handoff-keys"
        ("handoff takes exactly ${joinTokens requiredHandoffKeys}"
          + lib.optionalString (unknown != [ ]) "; unknown: ${joinTokens (sortUnique unknown)}"
          + lib.optionalString (missing != [ ]) "; missing: ${joinTokens (sortUnique missing)}"))
    ]
    else
      stringErrors "from"
      ++ stringErrors "adr"
      ++ lib.optional
        (builtins.isString handoff.from && lib.hasInfix "*" handoff.from)
        (err "handoff-from-wildcard"
          "handoff.from must name one registry name; there is no pattern form")
      ++ lib.optional
        (builtins.isString handoff.adr
          && (lib.hasPrefix "/" handoff.adr || lib.hasPrefix "~" handoff.adr))
        (err "handoff-adr-not-tracked"
          "handoff.adr must be a tracked repository-relative path, not an absolute or home path");

  idErrors = context: err: id:
    if !(builtins.isString id)
    then [ (err "id-not-a-string" "id must be a string") ]
    else if lib.hasPrefix "bundle:" id
    then
      lib.optional (!(matches bundleIdPattern (lib.removePrefix "bundle:" id)))
        (err "malformed-bundle-id"
          "a bundle identity is bundle:<reverse-DNS CFBundleIdentifier>, at least two segments of letters, digits and hyphens")
    else if lib.hasPrefix "mas:" id
    then
      let value = lib.removePrefix "mas:" id; in
      if !(matches masIdPattern value)
      then [ (err "malformed-mas-id" "a Mac App Store identity is mas:<decimal application id>") ]
      else lib.optional (lib.toInt value <= 0)
        (err "non-positive-mas-id" "a Mac App Store application id is a positive decimal number")
    else [
      (err "unsupported-identity-family"
        "identity must be bundle:<CFBundleIdentifier> or mas:<decimal id>; a display name or cask token is never an identity")
    ];

  schemaErrors = entry:
    let
      claim = entry.claim;
      context = describeRawClaim entry.owner claim;
      err = schemaError context;
      keys = builtins.attrNames claim;
      unknown = lib.subtractLists knownClaimKeys keys;
      missing = lib.subtractLists keys requiredClaimKeys;
    in
    if !(builtins.isAttrs claim)
    then [ (err "claim-not-an-attribute-set" "a claim must be an attribute set") ]
    else if unknown != [ ] || missing != [ ]
    then
      lib.optional (unknown != [ ])
        (err "unknown-keys"
          ("unknown keys: ${joinTokens (sortUnique unknown)}"
            + lib.optionalString (builtins.elem "owner" unknown)
              "; a module states what it installs, and the composition attaches owner identity"))
      ++ lib.optional (missing != [ ])
        (err "missing-keys" "missing required keys: ${joinTokens (sortUnique missing)}")
    else
      idErrors context err claim.id
      ++ lib.optional (!(builtins.elem claim.provider providers))
        (err "unsupported-provider"
          "provider must be one of ${joinTokens providers}; widening the set requires an ADR")
      ++ lib.optional (!(builtins.isString claim.package) || claim.package == "")
        (err "empty-package" "package must be the non-empty provider-native name")
      ++ lib.optional
        (claim.provider == "mas" && builtins.isString claim.package
          && !(matches masIdPattern claim.package))
        (err "malformed-mas-package"
          "a mas claim's package is the Mac App Store application id as a decimal string")
      ++ lib.optional
        (!(builtins.isString claim.artifact)
          || !(matches artifactPattern (normalizeArtifact claim.artifact)))
        (err "malformed-artifact"
          "artifact must be a bare application bundle file name such as \"VLC.app\"; a path is never an artifact")
      ++ lib.optional (claim ? suite && !(builtins.isBool claim.suite))
        (err "suite-not-a-boolean" "suite must be a boolean")
      ++ lib.optionals (claim ? handoff) (handoffErrors context err claim.handoff);

  normalize = entry:
    let claim = entry.claim; in
    {
      owner = entry.owner;
      id = claim.id;
      normalizedId = normalizeId claim.id;
      artifact = claim.artifact;
      normalizedArtifact = normalizeArtifact claim.artifact;
      provider = claim.provider;
      package = claim.package;
      normalizedPackage = normalizePackage claim.package;
      suite = claim.suite or false;
      handoff = claim.handoff or null;
    };

  describe = claim:
    "    ${claim.owner}  provider=${claim.provider}  package=${claim.package}  "
    + "artifact=${claim.artifact}  id=${claim.id}"
    + lib.optionalString (claim.handoff != null)
      "  handoff.from=${claim.handoff.from}  handoff.adr=${claim.handoff.adr}";

  collisionError = rule: subject: group: hint:
    "application claim collision (${rule})\n\n  ${subject}\n\n  claimed by:\n"
    + joinLines (lib.sort (a: b: a < b) (map describe group))
    + "\n\n  ${hint}";

  handoffHint =
    "Exactly one module may install an application. If this is a migration\n"
    + "  handoff, declare it on the destination module's claim:\n"
    + "    handoff = { from = \"<source registry name>\"; adr = \"<tracked decision path>\"; }";

  identityGroupErrors = normalizedId: group:
    let
      subject = "id: ${normalizedId}";
      fail = rule: collisionError rule subject group;
      count = builtins.length group;
      declared = builtins.filter (claim: claim.handoff != null) group;
    in
    if count == 1
    then
      lib.optional (declared != [ ])
        (fail "singleton-handoff"
          "a handoff records an overlap with a source owner's claim, but only one module claims this application; remove the handoff block")
    else if count > 2
    then [
      (fail "too-many-claims"
        "a handoff authorizes at most two claims for one application, and ${toString count} were found")
    ]
    else if declared == [ ]
    then [ (fail "duplicate-claim" handoffHint) ]
    else if builtins.length declared == 2
    then [
      (fail "mutual-handoff"
        "overlap is authorized in one direction by the destination; the source claim must carry no handoff of its own")
    ]
    else
      let
        destination = builtins.head declared;
        source = builtins.head (builtins.filter (claim: claim.handoff == null) group);
      in
      lib.optional (destination.handoff.from != source.owner)
        (fail "handoff-source-mismatch"
          "handoff.from=\"${destination.handoff.from}\" does not name the other claiming owner \"${source.owner}\"")
      ++ lib.optional (destination.owner == source.owner)
        (fail "handoff-same-owner"
          "a handoff transfers an application between two distinct owners")
      ++ lib.optional (destination.normalizedArtifact != source.normalizedArtifact)
        (fail "handoff-artifact-mismatch"
          "the two claims name different application bundles (\"${source.artifact}\" and \"${destination.artifact}\"); a handoff transfers one application, not two");

  artifactGroupErrors = normalizedArtifact: group:
    let ids = sortUnique (map (claim: claim.normalizedId) group); in
    lib.optional (builtins.length ids > 1)
      (collisionError "artifact-collision" "artifact: ${normalizedArtifact}" group
        ("these claims install different applications over the same bundle in /Applications; "
          + "identities present: ${joinTokens ids}"));

  providerPackageGroupErrors = key: group:
    let
      ids = sortUnique (map (claim: claim.normalizedId) group);
      subject = "provider/package: ${key}";
    in
    if builtins.length ids > 1
    then
      lib.optional (!(builtins.all (claim: claim.suite) group))
        (collisionError "provider-package-drift" subject group
          ("one provider package asserts ${toString (builtins.length ids)} application identities: ${joinTokens ids}. "
            + "If it genuinely supplies a suite of bundles, declare suite = true on every one of its claims."))
    else
      lib.optional (builtins.any (claim: claim.suite) group)
        (collisionError "stale-suite-marker" subject group
          "suite = true marks a provider package that supplies more than one application bundle; this one supplies a single application");

  groupErrors = keyOf: errorsOf: normalized:
    lib.concatLists (lib.mapAttrsToList errorsOf (lib.groupBy keyOf normalized));

  validate = entries:
    let
      schema = lib.concatMap schemaErrors entries;
      normalized = map normalize entries;
      cross =
        groupErrors (claim: claim.normalizedId) identityGroupErrors normalized
        ++ groupErrors (claim: claim.normalizedArtifact) artifactGroupErrors normalized
        ++ groupErrors
          (claim: "${claim.provider} ${claim.normalizedPackage}")
          providerPackageGroupErrors
          normalized;
      # A schema error suppresses cross-check reporting, because normalize
      # assumes string fields and would raise a raw type error over a malformed
      # claim. A caller therefore treats any non-ok result as fatal; the absence
      # of a particular rule token never means that rule holds.
      errors = if schema != [ ] then schema else cross;
    in
    {
      ok = errors == [ ];
      inherit errors;
    };

  assertValid = entries: value:
    let result = validate entries; in
    if result.ok
    then value
    else throw ("application claim validation failed\n\n"
      + lib.concatStringsSep "\n\n" result.errors + "\n");
in
{
  inherit
    assertValid
    attachOwner
    knownClaimKeys
    normalize
    normalizeArtifact
    normalizeId
    providers
    validate
    ;
}
